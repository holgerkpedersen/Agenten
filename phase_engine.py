import re
from typing import Any

from symbol_checks import check_symbols_covered_by_modules, PHASE_ALIASES, _resolve_phase_key, check_all_of
from file_checks import check_file_exists
from file_checks import check_files_from_plan
from text_tool_checks import check_text_contains, check_tests_pass
from text_tool_checks import check_min_text_length
from text_tool_checks import check_tool_called
from text_tool_checks import check_code_contains
from phase_engine import check_tests_pass


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
