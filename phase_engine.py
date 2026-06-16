import re
from typing import Any



def check_tests_pass(spec: dict[str, Any], agent: Any | None = None, tool_name: str = "", called_tools: dict | None = None) -> tuple[bool, str]:
    """Return ``(passed, message)`` for a tests_pass check.

    The check has three guards:

      1. ``require_run`` (default True) — fail unless the LLM actually
         called ``run_tests`` (we use ``tool_name`` for the latest call
         and fall back to scanning ``called_tools`` keys).
      2. ``agent._tests_failed`` must be ``False`` (or absent).
      3. ``agent._last_test_summary`` (if present) should not contain
         substrings like ``failed`` or ``error``.

    This is the only way a Test phase auto-completes — declaring
    ``<<<DONE>>>`` without invoking ``run_tests`` does not pass.

    Spec keys:
      - ``scope`` (default "all") — informational only; current
        implementation only supports the project-wide test suite.
      - ``require_run`` (default True) — gate on LLM having invoked
        ``run_tests``.
    """
    require_run = bool(spec.get("require_run", True))
    if require_run:
        ran = tool_name == "run_tests"
        if not ran and called_tools:
            ran = any(k.split("{")[0] == "run_tests" for k in called_tools)
        if not ran:
            return False, "tests_pass: LLM kaldte ikke run_tests i denne fase"
    if agent is not None and getattr(agent, "_tests_failed", False):
        return False, "tests_pass: seneste testkørsel fejlede"
    summary = ""
    if agent is not None:
        summary = getattr(agent, "_last_test_summary", "") or ""
    if summary and re.search(r"\b(failed|error)\b", summary, re.IGNORECASE):
        return False, f"tests_pass: test summary nævner fejl: {summary[:120]}"
    return True, "tests_pass: alle tests bestod"

from symbol_checks import check_symbols_covered_by_modules
from file_checks import check_file_exists
from file_checks import check_files_from_plan
from text_tool_checks import check_text_contains
from text_tool_checks import check_min_text_length
from text_tool_checks import check_tool_called
from text_tool_checks import check_code_contains
from phase_engine import check_tests_pass



def check_all_of(
    spec: dict[str, Any],
    agent: Any | None = None,
    task_node: Any | None = None,
    called_tools: dict | None = None,
    base_dir: str | None = None,
    tool_name: str = "",
    full_response: str = "",
) -> tuple[bool, str]:
    """Return ``(passed, message)`` for an all_of compound check.

    Runs each sub-spec in ``spec["checks"]`` (list) and passes only if all
    pass. Useful when a phase needs to satisfy more than one criterion
    (e.g. refactor Ekstraher needs both ``files_from_plan`` AND
    ``symbols_covered``).

    Spec keys:
      - ``checks`` (required) — list of sub-specs. Each must have a
        ``"type"`` key matching one of the supported check types.
      - ``fail_fast`` (default True) — stop at the first failing sub-check
        (the message reports the failing type).
    """
    sub_specs = spec.get("checks", []) or []
    if not sub_specs:
        return False, "all_of: ingen sub-checks"
    fail_fast = bool(spec.get("fail_fast", True))
    passed_types: list[str] = []
    for sub in sub_specs:
        if not isinstance(sub, dict):
            continue
        sub_type = sub.get("type")
        if sub_type == "file_exists":
            r = check_file_exists(sub.get("paths", []), sub, base_dir=base_dir)
        elif sub_type == "files_from_plan":
            r = check_files_from_plan(sub, base_dir=base_dir)
        elif sub_type == "min_text_length":
            r = check_min_text_length(sub, full_response=full_response, agent=agent)
        elif sub_type == "code_contains":
            r = check_code_contains(sub, base_dir=base_dir)
        elif sub_type == "tool_called":
            r = check_tool_called(sub, tool_name=tool_name, called_tools=called_tools)
        elif sub_type == "tests_pass":
            r = check_tests_pass(sub, agent=agent, tool_name=tool_name, called_tools=called_tools)
        elif sub_type == "symbols_covered":
            r = check_symbols_covered_by_modules(sub, base_dir=base_dir)
        elif sub_type == "text_contains":
            r = check_text_contains(sub, full_response=full_response)
        else:
            r = (False, f"unknown sub-check: {sub_type}")
        if r[0]:
            passed_types.append(sub_type)
            continue
        if fail_fast:
            return False, f"all_of: {sub_type} fejlede — {r[1]}"
        return False, f"all_of: {sub_type} fejlede — {r[1]}"
    return True, f"all_of: alle {len(passed_types)} sub-checks bestod ({', '.join(passed_types)})"



def _resolve_phase_key(phase_name: str, template_checks: dict[str, dict[str, Any]]) -> str | None:
    """Find the canonical key in template_checks matching *phase_name*.

    Checks direct (case-insensitive) match first, then alias lookups.
    Returns the actual key from *template_checks* or None.
    """
    lowered = phase_name.lower()
    for key in template_checks:
        if key.lower() == lowered:
            return key
    # Alias lookup: find the canonical alias key, then find the matching
    # template_checks entry (case-insensitive).
    for alias_key, aliases in PHASE_ALIASES.items():
        if lowered == alias_key or lowered in aliases:
            for key in template_checks:
                if key.lower() == alias_key:
                    return key
    return None



# Phase name aliases for multi-language support.
# Maps canonical (Danish) phase key → list of alias phase names in other languages.
# Used by both backend check_phase_done() and the frontend /api/phase-checks endpoint.
PHASE_ALIASES: dict[str, list[str]] = {
    "analyse": ["analysis", "análisis", "分析"],
    "ekstraher": ["extract", "extraer", "提取"],
    "opdatér": ["update", "actualizar", "更新"],
    "test": ["probar", "测试"],
    "test (red)": ["prueba (red)", "测试 (red)"],
    "implementering": ["implementation", "implementación", "实施"],
    "verifikation (green)": ["verification (green)", "verificación (green)", "验证 (green)"],
    "opdatering": ["update", "actualización", "更新"],
    "læs": ["read"],
    "afklar": ["clarify"],
    "luk": ["close"],
}



TEMPLATE_PHASE_CHECKS: dict[str, dict[str, dict[str, Any]]] = {
    "refactor": {
        "Analyse": {
            "type": "all_of",
            "description": "FORM\u00c5L: Forst\u00e5 den store fils struktur og ansvarsomr\u00e5der. Kr\u00e6ver: mindst 500 tegn analyse + 3 funktioner l\u00e6st med read_location.",
            "description_key": "phase_check.refactor.analyse",
            "checks": [
                {"type": "min_text_length", "min_chars": 500},
                {"type": "tool_called", "tools": ["read_location"], "min_count": 3},
            ],
        },
        "Plan": {
            "type": "files_from_plan",
            "plan_path": "refactor_plan.md",
            "ext": ".py",
            "min_files": 5,
            "description": "FORM\u00c5L: Beslut modulopdeling og skriv plan. Kr\u00e6ver: refactor_plan.md med mindst 5 *.py-moduler.",
            "description_key": "phase_check.refactor.plan",
        },
        "Ekstraher": {
            "type": "all_of",
            "description": "FORM\u00c5L: Opret nye modulfiler med kode fra den originale fil. Kr\u00e6ver: alle planlagte *.py-moduler oprettet + alle symboler fordelt.",
            "description_key": "phase_check.refactor.ekstraher",
            "checks": [
                {
                    "type": "files_from_plan",
                    "plan_path": "refactor_plan.md",
                    "ext": ".py",
                    "min_files": 1,
                },
                {
                    "type": "symbols_covered",
                    "source_file": "api_server.py",
                    "plan_path": "refactor_plan.md",
                    "ext": ".py",
                    "exclude_patterns": [r"^__[A-Za-z0-9_]+__$"],
                },
            ],
        },
        "Opdat\u00e9r": {
            "type": "code_contains",
            "path": "api_server.py",
            "patterns": [
                "from\\s+routes\\b",
                "from\\s+session_manager\\b",
                "from\\s+file_handler\\b",
                "from\\s+image_handler\\b",
                "from\\s+model_manager\\b",
                "from\\s+security\\b",
                "from\\s+helpers\\b",
            ],
            "require_all": False,
            "min_matches": 1,
            "description": "FORM\u00c5L: Fjern flyttet kode fra original fil, tilf\u00f8j imports til nye moduler. Kr\u00e6ver: api_server.py importerer fra mindst \u00e9t nyt modul.",
            "description_key": "phase_check.refactor.opdater",
        },
        "Test": {
            "type": "tests_pass",
            "scope": "all",
            "description": "FORM\u00c5L: Bekr\u00e6ft at refactoring ikke har brudt noget. Kr\u00e6ver: alle tests best\u00e5r.",
            "description_key": "phase_check.refactor.test",
        },
    },
    "bugfix": {
        "Analyse": {
            "type": "min_text_length",
            "min_chars": 300,
            "description": "FORM\u00c5L: Forst\u00e5 buggen og identific\u00e9r rod\u00e5rsag i koden. Kr\u00e6ver: mindst 300 tegn analyse.",
            "description_key": "phase_check.bugfix.analyse",
        },
        "Test (Red)": {
            "type": "file_exists",
            "paths": ["tests/temp/test_*.py"],
            "require_all": False,
            "min_files": 1,
            "description": "FORM\u00c5L: Skriv en pytest der reproducerer buggen. Kr\u00e6ver: test-fil i tests/temp/. Testen skal fejle (r\u00f8d fase).",
            "description_key": "phase_check.bugfix.test_red",
        },
        "Implementering": {
            "type": "tool_called",
            "tools": ["edit_file", "write_file"],
            "description": "FORM\u00c5L: Ret koden med minimal \u00e6ndring. Kr\u00e6ver: edit_file eller write_file kaldt.",
            "description_key": "phase_check.bugfix.implementering",
        },
        "Verifikation (Green)": {
            "type": "tests_pass",
            "scope": "all",
            "description": "FORM\u00c5L: Bekr\u00e6ft at fixet virker og ingen regressions. Kr\u00e6ver: alle tests best\u00e5r.",
            "description_key": "phase_check.bugfix.verifikation",
        },
        "Opdatering": {
            "type": "tool_called",
            "tools": ["update_issue_status"],
            "description": "FORM\u00c5L: Luk issue med beskrivelse af hvad der blev rettet. Kr\u00e6ver: update_issue_status kaldt.",
            "description_key": "phase_check.bugfix.opdatering",
        },
    },
    "issue_handler": {
        "L\u00e6s": {
            "type": "tool_called",
            "tools": ["read_issue"],
            "description": "FORM\u00c5L: L\u00e6s issue-beskrivelsen og forst\u00e5 problemet. Kr\u00e6ver: read_issue kaldt.",
            "description_key": "phase_check.issue_handler.laes",
        },
        "Afklar": {
            "type": "min_text_length",
            "min_chars": 200,
            "description": "FORM\u00c5L: Analys\u00e9r koden, afg\u00f8r om fejlen findes. Kr\u00e6ver: mindst 200 tegn analyse.",
            "description_key": "phase_check.issue_handler.afklar",
        },
        "Fix": {
            "type": "tool_called",
            "tools": ["edit_file", "write_file"],
            "description": "FORM\u00c5L: Ret fejlen i koden. Kr\u00e6ver: edit_file eller write_file kaldt.",
            "description_key": "phase_check.issue_handler.fix",
        },
        "Luk Issue": {
            "type": "tool_called",
            "tools": ["update_issue_status"],
            "description": "FORM\u00c5L: Mark\u00e9r issue som resolved med rettelsesnote. Kr\u00e6ver: update_issue_status kaldt.",
            "description_key": "phase_check.issue_handler.luk",
        },
    },
    "testgenerering": {
        "Analyse": {
            "type": "min_text_length",
            "min_chars": 300,
            "description": "FORM\u00c5L: Forst\u00e5 hvilke funktioner der mangler testd\u00e6kning. Kr\u00e6ver: mindst 300 tegn analyse.",
            "description_key": "phase_check.testgenerering.analyse",
        },
        "Test (Red)": {
            "type": "file_exists",
            "paths": ["tests/temp/test_*.py"],
            "require_all": False,
            "min_files": 1,
            "description": "FORM\u00c5L: Skriv pytest-tests for den manglende d\u00e6kning. Kr\u00e6ver: test-fil i tests/temp/.",
            "description_key": "phase_check.testgenerering.test_red",
        },
        "Implementering": {
            "type": "tool_called",
            "tools": ["edit_file"],
            "optional": True,
            "description": "FORM\u00c5L: G\u00f8r koden testbar hvis n\u00f8dvendigt. Kr\u00e6ver: edit_file kaldt (kun hvis koden skal \u00e6ndres).",
            "description_key": "phase_check.testgenerering.implementering",
        },
        "Verifikation (Green)": {
            "type": "tests_pass",
            "scope": "all",
            "description": "FORM\u00c5L: Bekr\u00e6ft at nye tests best\u00e5r og ingen regressions. Kr\u00e6ver: alle tests best\u00e5r.",
            "description_key": "phase_check.testgenerering.verifikation",
        },
    },
    "kodeanalyse": {
        "Form\u00e5l": {
            "type": "all_of",
            "description": "FORM\u00c5L: Forklar hvad filen g\u00f8r og dens rolle i projektet. Kr\u00e6ver: fil gemt i docs/formaal.md med analyse af form\u00e5l, ansvar, cohesion og single responsibility.",
            "description_key": "phase_check.kodeanalyse.formaal",
            "checks": [
                {"type": "file_exists", "paths": ["docs/formaal.md"], "min_files": 1},
                {"type": "code_contains", "path": "docs/formaal.md", "patterns": [
                    "form\u00e5l", "ansvar", "rolle", "cohesion", "single responsibility"
                ], "min_matches": 3},
            ],
        },
        "Imports og afh\u00e6ngigheder": {
            "type": "all_of",
            "description": "FORM\u00c5L: Gennemg\u00e5 filens imports og eksterne afh\u00e6ngigheder. Kr\u00e6ver: fil gemt i docs/imports.md med gennemgang af imports, cirkul\u00e6re afh\u00e6ngigheder og ubrugte imports.",
            "description_key": "phase_check.kodeanalyse.imports",
            "checks": [
                {"type": "file_exists", "paths": ["docs/imports.md"], "min_files": 1},
                {"type": "code_contains", "path": "docs/imports.md", "patterns": [
                    "import", "afh\u00e6ngighed", "cirkul\u00e6r", "ubrugt", "ekstern"
                ], "min_matches": 3},
            ],
        },
        "Arkitektur": {
            "type": "all_of",
            "description": "FORM\u00c5L: Analys\u00e9r filens struktur, design patterns og dataflow. Kr\u00e6ver: fil gemt i docs/arkitektur.md med analyse af struktur, patterns, coupling og SOLID.",
            "description_key": "phase_check.kodeanalyse.arkitektur",
            "checks": [
                {"type": "file_exists", "paths": ["docs/arkitektur.md"], "min_files": 1},
                {"type": "code_contains", "path": "docs/arkitektur.md", "patterns": [
                    "klasse", "funktion", "struktur", "design pattern", "kobling",
                    "cohesion", "SOLID", "single responsibility"
                ], "min_matches": 4},
            ],
        },
        "Kodekvalitet": {
            "type": "all_of",
            "description": "FORM\u00c5L: Vurder kodekvalitet (DRY, SOLID, PEP 8, complexity, naming, tests). Kr\u00e6ver: fil gemt i docs/kodekvalitet.md med kvalitetsvurdering.",
            "description_key": "phase_check.kodeanalyse.kvalitet",
            "checks": [
                {"type": "file_exists", "paths": ["docs/kodekvalitet.md"], "min_files": 1},
                {"type": "code_contains", "path": "docs/kodekvalitet.md", "patterns": [
                    "DRY", "SOLID", "PEP 8", "navngivning", "complexity",
                    "type hint", "fejlh\u00e5ndtering", "test coverage"
                ], "min_matches": 4},
            ],
        },
        "Sikkerhed": {
            "type": "all_of",
            "description": "FORM\u00c5L: Identific\u00e9r s\u00e5rbarheder (OWASP top 10). Kr\u00e6ver: fil gemt i docs/sikkerhed.md med sikkerhedsanalyse.",
            "description_key": "phase_check.kodeanalyse.sikkerhed",
            "checks": [
                {"type": "file_exists", "paths": ["docs/sikkerhed.md"], "min_files": 1},
                {"type": "code_contains", "path": "docs/sikkerhed.md", "patterns": [
                    "inputvalidering", "autentifikation", "access control",
                    "kryptering", "fejlh\u00e5ndtering", "session",
                    "CSRF", "XSS", "SQL injection", "OWASP"
                ], "min_matches": 5},
            ],
        },
    },
    "programmering": {
        "Kravanalyse": {
            "type": "file_exists",
            "paths": ["docs/kravanalyse.md"],
            "min_files": 1,
            "description": "FORM\u00c5L: Afd\u00e6k og dokument\u00e9r alle krav (funktionelle og ikke-funktionelle). Kr\u00e6ver: docs/kravanalyse.md eksisterer.",
            "description_key": "phase_check.programmering.kravanalyse",
        },
        "Arkitekturdesign": {
            "type": "file_exists",
            "paths": ["docs/arkitektur.md"],
            "min_files": 1,
            "description": "FORM\u00c5L: Design systemarkitektur med komponenter, moduler og dataflow. Kr\u00e6ver: docs/arkitektur.md eksisterer.",
            "description_key": "phase_check.programmering.arkitekturdesign",
        },
        "Implementeringsplan": {
            "type": "file_exists",
            "paths": ["docs/implementeringsplan.md"],
            "min_files": 1,
            "description": "FORM\u00c5L: Planl\u00e6g filstruktur, r\u00e6kkef\u00f8lge og teststrategi. Kr\u00e6ver: docs/implementeringsplan.md eksisterer.",
            "description_key": "phase_check.programmering.implementeringsplan",
        },
        "Sikkerhedsanalyse": {
            "type": "file_exists",
            "paths": ["docs/sikkerhedsanalyse.md"],
            "min_files": 1,
            "description": "FORM\u00c5L: Analys\u00e9r sikkerhedsaspekter (OWASP, inputvalidering, auth). Kr\u00e6ver: docs/sikkerhedsanalyse.md eksisterer.",
            "description_key": "phase_check.programmering.sikkerhedsanalyse",
        },
        "Uddyb/refinements": {
            "type": "file_exists",
            "paths": [
                "docs/uddybning_dialog.md",
                "docs/kravanalyse.md",
                "docs/arkitektur.md",
                "docs/implementeringsplan.md",
                "docs/sikkerhedsanalyse.md",
            ],
            "min_files": 5,
            "description": "FORM\u00c5L: Identificer manglende specifikationer, lad LLM svare, opdater docs. Kr\u00e6ver: docs/uddybning_dialog.md eksisterer OG alle 4 originale docs er bevaret.",
            "description_key": "phase_check.programmering.uddyb",
        },
        "Kodeimplementering": {
            "type": "files_from_plan",
            "plan_path": "docs/implementeringsplan.md",
            "min_files": 5,
            "allow_nested": True,
            "description": "FORM\u00c5L: Skriv koden baseret p\u00e5 design og plan. Kr\u00e6ver: alle moduler n\u00e6vnt i docs/implementeringsplan.md er oprettet (greenfield).",
            "description_key": "phase_check.programmering.kodeimplementering",
        },
    },
    "selvforbedring": {
        "Analyser": {
            "type": "tool_called",
            "tools": ["read_issue"],
            "description": "FORM\u00c5L: L\u00e6s CORE-issue og forst\u00e5 hotspot. Kr\u00e6ver: read_issue kaldt.",
            "description_key": "phase_check.selvforbedring.analyser",
        },
        "Diagnostic\u00e9r": {
            "type": "tool_called",
            "tools": ["run_tests"],
            "description": "FORM\u00c5L: Bekr\u00e6ft fejlm\u00f8nster med tests. Kr\u00e6ver: run_tests kaldt.",
            "description_key": "phase_check.selvforbedring.diagnosticer",
        },
        "Ret": {
            "type": "tool_called",
            "tools": ["edit_file"],
            "description": "FORM\u00c5L: Redig\u00e9r koden med AST-baseret edit. Kr\u00e6ver: edit_file kaldt.",
            "description_key": "phase_check.selvforbedring.ret",
        },
        "Verific\u00e9r": {
            "type": "all_of",
            "description": "FORM\u00c5L: K\u00f8r tests og mark\u00e9r issue som resolved. Kr\u00e6ver: tests best\u00e5et + update_issue_status kaldt.",
            "description_key": "phase_check.selvforbedring.verificer",
            "checks": [
                {"type": "tests_pass"},
                {"type": "tool_called", "tools": ["update_issue_status"]},
            ],
        },
        "Commit": {
            "type": "tool_called",
            "tools": ["git_commit"],
            "description": "FORM\u00c5L: Commit og push \u00e6ndringerne. Kr\u00e6ver: git_commit kaldt.",
            "description_key": "phase_check.selvforbedring.commit",
        },
    },
}

from phase_engine import check_all_of
from phase_engine import _resolve_phase_key
from phase_engine import TEMPLATE_PHASE_CHECKS



def check_phase_done(agent: Any, task_node: Any, called_tools: dict | None = None, base_dir: str | None = None, tool_name: str = "", full_response: str = "") -> tuple[bool, str]:
    """Check whether the current phase should auto-complete.

    Looks up the phase's check spec in ``TEMPLATE_PHASE_CHECKS`` and runs it.
    Returns ``(passed, message)``. If no spec is defined for the current
    template/phase, returns ``(False, "")`` so existing LLM-driven flow
    continues.

    Args:
        agent: Agent instance (for template/lang, ``_tests_failed`` flag,
            ``messages`` for ``min_text_length``, ``active_template``).
        task_node: Current task node (for phase name).
        called_tools: Optional dict of tool-key -> call count. Tool keys are
            ``"{tool_name}{args_dict_repr}"``; we extract the tool name by
            splitting on ``{``.
        base_dir: Optional base directory to resolve relative paths against.
        tool_name: Name of the most recent tool call. Used by
            ``tool_called`` and ``tests_pass`` checks to detect "the LLM
            just called this tool" without scanning the full history.
        full_response: Accumulated LLM text output (streaming). Used by
            ``min_text_length`` to count characters without scanning
            ``agent.messages`` (faster and works even when messages list
            is empty).
    """
    template = getattr(agent, "active_template", "") or ""
    if not template:
        return False, ""
    template_checks = TEMPLATE_PHASE_CHECKS.get(template)
    if not template_checks:
        return False, ""
    phase_name = (task_node.name or "").strip()
    canonical_key = _resolve_phase_key(phase_name, template_checks)
    if not canonical_key:
        return False, ""
    spec = template_checks.get(canonical_key)
    if not spec:
        return False, ""
    check_type = spec.get("type")
    if check_type == "file_exists":
        return check_file_exists(spec.get("paths", []), spec, base_dir=base_dir)
    if check_type == "files_from_plan":
        return check_files_from_plan(spec, base_dir=base_dir)
    if check_type == "min_text_length":
        return check_min_text_length(spec, full_response=full_response, agent=agent)
    if check_type == "code_contains":
        return check_code_contains(spec, base_dir=base_dir)
    if check_type == "tool_called":
        return check_tool_called(spec, tool_name=tool_name, called_tools=called_tools)
    if check_type == "tests_pass":
        return check_tests_pass(spec, agent=agent, tool_name=tool_name, called_tools=called_tools)
    if check_type == "symbols_covered":
        return check_symbols_covered_by_modules(spec, base_dir=base_dir)
    if check_type == "text_contains":
        return check_text_contains(spec, full_response=full_response)
    if check_type == "all_of":
        return check_all_of(
            spec, agent=agent, task_node=task_node, called_tools=called_tools,
            base_dir=base_dir, tool_name=tool_name, full_response=full_response,
        )
    return False, f"unknown check type: {check_type}"
