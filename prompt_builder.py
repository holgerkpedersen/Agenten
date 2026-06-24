import os
import re
from i18n import K
from lang import t
import agent_skills
from typing import Any, Generator
from config_utils import log, EXECUTION_TIMEOUT, _WRITE_TOOLS, FRAMEWORK_PY, _get_max_tool_calls, _get_max_iterations, _set_phase_model, _is_greenfield
from phase_manager import _normalize_phase, PHASE_ALIASES, CLOSE_PHASE_ALIASES, _get_phase_auto_complete_msg, _generate_phase_todos
import agent_files
import json

def _build_initial_messages(agent: Any, task_node: Any, original_prompt: str, chunk_hint: str) -> tuple[list[dict], str, bool]:
    """build initial messages.
    
    Args:
        agent:
        task_node:
        original_prompt:
        chunk_hint:"""
    clean_prompt = getattr(agent, 'prompt', original_prompt)
    file_ctx = getattr(agent, '_file_context_str', '')

    # Maintenance mode: use vedligeholdelse section instruction when .py files exist
    phase_name = task_node.name or ""
    maintenance_key = phase_name + " (vedligeholdelse)"
    is_maintenance = (
        agent.active_template == "programmering"
        and "kodeimplementering" in _normalize_phase(phase_name)
        and not _is_greenfield()
    )
    if is_maintenance:
        section_instr = agent_skills.SECTION_INSTRUCTIONS.get(agent.active_template, {}).get(maintenance_key, "")
    else:
        section_instr = agent_skills.SECTION_INSTRUCTIONS.get(agent.active_template, {}).get(agent.lang + "_" + phase_name.lower(), "") or \
                        agent_skills.SECTION_INSTRUCTIONS.get(agent.active_template, {}).get(phase_name.lower(), "") or \
                        agent_skills.SECTION_INSTRUCTIONS.get(agent.active_template, {}).get(phase_name, "") or \
                        agent_skills.SECTION_INSTRUCTIONS.get(agent.active_template, {}).get(_normalize_phase(phase_name), "")
    # Replace {source_file} placeholder in section instructions with the actual
    # target file from the prompt (e.g. refac_test.py).
    if section_instr and "{source_file}" in section_instr:
        _file_match = re.search(r"([a-zA-Z_][\w.]+\.py)", original_prompt or "")
        if _file_match:
            section_instr = section_instr.replace("{source_file}", _file_match.group(1))
    criteria_block = ""
    header = t(K.CRITERIA_HEADER, agent.lang)
    if task_node.success_criteria:
        items = "\n".join(f"- {c}" for c in task_node.success_criteria)
        criteria_block = f"\n\n## {header}\n{items}\n"
    elif section_instr and agent.active_template:
        lines = [l.strip() for l in section_instr.split("\n") if l.strip() and not l.startswith("Afslut")]
        criteria_text = "\n".join(f"- {l}" for l in lines[:6])
        if len(lines) > 6:
            criteria_text += "\n- ..."
        criteria_block = f"\n\n## {header}\n{criteria_text}\n"

    # Include results from previous sibling phases
    sibling_block = ""
    if task_node.parent and hasattr(task_node.parent, 'children'):
        siblings = task_node.parent.children
        my_idx = -1
        for i, sib in enumerate(siblings):
            if sib == task_node:
                my_idx = i
                break
        if my_idx > 0:
            prev_results = []
            for sib in siblings[:my_idx]:
                if sib.status == "done" and sib.result and len(sib.result.strip()) > 50:
                    prev_results.append(f"### {sib.name}\n{sib.result.strip()}")
            if prev_results:
                sibling_block = "\n\n## Resultater fra tidligere faser\n" + "\n\n".join(prev_results)

    plan_block = ""

    # For refactor template's Plan phase, auto-load refactor_analyse.md
    # so the LLM doesn't waste iterations re-reading symbol/functions.
    if (agent.active_template == "refactor" and
        task_node.name.lower() == "plan" and
        not any("refactor_analyse" in str(c) for c in agent.file_chunks.values())):
        _analyse_path = "refactor_analyse.md"
        _wd = os.environ.get('AGENT_WORKDIR', '')
        if _wd:
            _analyse_path = os.path.join(_wd, _analyse_path)
        if os.path.exists(_analyse_path):
            try:
                with open(_analyse_path, encoding="utf-8") as _af:
                    _analyse_content = _af.read()
                plan_block = "\n\n📄 **Analyse fra forrige fase (auto-indlæst):**\n```\n" + _analyse_content[:3000] + "\n```\n\nBrug denne analyse som grundlag — du behøver IKKE læse symboler eller funktioner igen."
                agent._log("DEBUG", f"Auto-loaded {_analyse_path} ({len(_analyse_content)} chars) for Plan", "")
            except Exception as _e:
                agent._log("DEBUG", f"Failed to auto-load analyse: {_e}", "")

    # For refactor template's Ekstraher and Opdatér phases, auto-load
    # refactor_plan.md + symbol-status so the LLM knows EXACTLY which
    # symbols need extraction/cleanup — no list_symbols needed.
    if not plan_block and (agent.active_template == "refactor" and
                           task_node.name.lower() in ("ekstraher", "opdatér") and
                           not any("plan.md" in str(c) for c in agent.file_chunks.values())):
        plan_path = getattr(agent, '_refactor_plan_path', '') or "refactor_plan.md"
        if os.path.exists(plan_path):
            try:
                with open(plan_path, encoding="utf-8") as _pf:
                    _plan_content = _pf.read()
                plan_block = "\n\n" + t(K.REFACTOR_PLAN_LOADED, agent.lang).format(
                    plan_content=_plan_content[:3000]
                )
                plan_block += _build_refactor_phase_context(agent)
                agent._log("DEBUG", f"Auto-loaded {plan_path} ({len(_plan_content)} chars) + symbol status for {task_node.name}", "")
            except Exception as _e:
                agent._log("DEBUG", f"Failed to auto-load refactor context: {_e}", "")

    # For refactor Ekstraher phase: auto-suggest module groups from dependency graph.
    # Only when the plan doesn't already have detailed per-module symbol lists.
    _group_block = ""
    if agent.active_template == "refactor" and task_node.name.lower() == "ekstraher":
        _plan_path = getattr(agent, '_refactor_plan_path', '') or "refactor_plan.md"
        _plan_has_details = False
        if os.path.exists(_plan_path):
            from symbol_checks import _parse_plan_symbol_mapping as _spm
            try:
                with open(_plan_path, encoding="utf-8") as _pf:
                    _plan_has_details = bool(_spm(_pf.read()))
            except Exception:
                pass
        if not _plan_has_details:
            try:
                from refactoring_engine import RefactoringEngine
                # Determine source file from prompt
                _src_match = re.search(r"([a-zA-Z_][\w.]+\.py)", original_prompt or "")
                _source_file = _src_match.group(1) if _src_match else "api_server.py"
                agent._source_file = _source_file
                _engine = RefactoringEngine()
                _gr = _engine.suggest_module_groups(source=_source_file, max_group_size=8)
                if _gr.get("success") and _gr.get("groups"):
                    # Filter groups to only include symbols that actually exist in the file
                    # (suggest_module_groups may include already-extracted symbols from imports)
                    import agent_files as _af
                    _existing = set()
                    _ls = _af.list_symbols(filepath=_source_file)
                    if _ls.get("success"):
                        _existing = {s["name"] for s in _ls.get("symbols", [])}
                    if _existing:
                        for _g in _gr["groups"]:
                            _g["symbols"] = [s for s in _g.get("symbols", []) if s in _existing]
                        _gr["groups"] = [_g for _g in _gr["groups"] if _g.get("symbols")]

                    _lines = ["\n## Foresl\u00e5ede modulopdelinger (fra afh\u00e6ngighedsgraf)"]
                    for i, g in enumerate(_gr["groups"], 1):
                        _syms = g.get("symbols", [])
                        if isinstance(_syms, (list, tuple)):
                            _sym_list = ", ".join(str(s) for s in _syms[:12])
                            if len(_syms) > 12:
                                _sym_list += f" ... (+{len(_syms)-12})"
                            _lines.append(f"\n  Gruppe {i} ({len(_syms)} symboler): {_sym_list}")
                    _group_block = "\n".join(_lines)
                    agent._log("DEBUG", f"Auto-generated {len(_gr['groups'])} module groups for {_source_file}", "")
            except Exception as _e:
                agent._log("DEBUG", f"Could not suggest module groups: {_e}", "")

    # For refactor phases: inject full list_symbols output so model never needs to call it
    _symbols_block = ""
    if agent.active_template == "refactor" and task_node.name.lower() in ("ekstraher", "opdatér"):
        try:
            import agent_files as _af
            _src_match = re.search(r"([a-zA-Z_][\w.]+\.py)", original_prompt or "")
            _source_file = _src_match.group(1) if _src_match else "api_server.py"
            agent._source_file = _source_file
            _ls = _af.list_symbols(filepath=_source_file)
            if _ls.get("success") and _ls.get("symbols"):
                _lines = [f"\n## Symboler i {_source_file} (auto-loaded)"]
                for _sym in _ls["symbols"]:
                    _name = _sym.get("name", "?")
                    _type = _sym.get("type", "?")
                    _sig = _sym.get("signature", "")
                    _line = _sym.get("line", "")
                    if _sig:
                        _lines.append(f"  {_type} {_sig}")
                    elif _line:
                        _lines.append(f"  {_type} {_name} (linje {_line})")
                    else:
                        _lines.append(f"  {_type} {_name}")
                    for _m in (_sym.get("methods") or []):
                        _m_sig = _m.get("signature", "")
                        if _m_sig:
                            _lines.append(f"    {_m_sig}")
                        else:
                            _lines.append(f"    def {_m.get('name','')} (linje {_m.get('line','')})")
                _symbols_block = "\n".join(_lines)
                agent._log("DEBUG", f"Auto-loaded {len(_ls['symbols'])} symbols from {_source_file} into prompt", "")
        except Exception as _e:
            agent._log("DEBUG", f"Could not inject symbols block: {_e}", "")

    # For programming template's later phases, auto-load docs from earlier phases.
    PROGRAMMING_DOCS = [
        ("docs/kravanalyse.md", "Kravanalyse"),
        ("docs/arkitektur.md", "Arkitekturdesign"),
        ("docs/implementeringsplan.md", "Implementeringsplan"),
        ("docs/sikkerhedsanalyse.md", "Sikkerhedsanalyse"),
    ]
    PHASE_ORDER = ["kravanalyse", "arkitekturdesign", "implementeringsplan", "sikkerhedsanalyse", "kodeimplementering"]
    if agent.active_template == "programmering" and not plan_block:
        workdir = os.environ.get('AGENT_WORKDIR') or os.getcwd()
        current_idx = -1
        task_lower = task_node.name.lower() if task_node.name else ""
        for i, p in enumerate(PHASE_ORDER):
            if p in task_lower:
                current_idx = i
                break
        if current_idx > 0:
            loaded_blocks = []
            for doc_path, doc_phase in PROGRAMMING_DOCS[:current_idx]:
                full_path = os.path.join(workdir, doc_path)
                if os.path.exists(full_path):
                    try:
                        with open(full_path, encoding="utf-8") as _df:
                            _content = _df.read()
                        loaded_blocks.append(
                            f"### {doc_phase} — {doc_path}\n\n{_content[:4000]}"
                        )
                    except Exception as _e:
                        agent._log("DEBUG", f"Failed to load {doc_path}: {_e}", "")
            if loaded_blocks:
                plan_block = "\n\n## Dokumenter fra tidligere faser (ALLEREDE INDLÆST — behøver IKKE read_chunk)\n" + "\n\n".join(loaded_blocks)
                agent._log("DEBUG", f"Auto-loaded {len(loaded_blocks)} previous phase docs for {task_node.name}", "")

    # Phase anchor: tell the LLM which phase it's currently in and forbid
    # cross-phase reasoning (the LLM otherwise tries to re-do Plan/Extract/etc.
    # because it sees the workflow description in the section instruction).
    phase_block = "\n\n" + t(K.PHASE_CURRENT, agent.lang).format(phase_name=task_node.name) + \
                  t(K.PHASE_ONLY, agent.lang).format(phase_name=task_node.name)

    # Build reason block — context about WHY this phase exists
    reason_block = _build_phase_reason(getattr(agent, 'active_template', ''), task_node.name, original_prompt)
    if not reason_block:
        reason_block = ""

    # For refactor Ekstraher: trust instruction — LLM already has plan + symbols
    # Triggers when EITHER refactor_plan.md is loaded (plan_block) OR
    # auto-generated module groups are present (_group_block).
    _trust_block = ""
    if (agent.active_template == "refactor"
        and task_node.name.lower() == "ekstraher"
        and (plan_block or _group_block)
        and _symbols_block):
        _trust_block = (
            "\n\n\U0001f512 DU HAR ALLEREDE ALLE DATA I PROMPTEN OVENFOR."
            "\nSymbol-listen OG modulopdelingerne er allerede indl\u00e6st."
            "\nDu beh\u00f8ver IKKE at kalde list_symbols \u2014 du har ALLE symboler i prompten."
            "\nG\u00e5 DIREKTE til batch_extract_symbols med symbol-grupperne nedenfor."
            "\nKald IKKE list_symbols f\u00f8r du har pr\u00f8vet batch_extract_symbols."
        )

    # Når trust-block er aktiv: fjern list_symbols fra active tools så LLM'en
    # slet ikke kan kalde det — den er nødt til at bruge batch_extract_symbols.
    if _trust_block:
        _active = getattr(agent, 'tool_registry', None)
        if _active and _active.active_tools:
            _active.active_tools = [t for t in _active.active_tools if t != "list_symbols"]
            agent._log("DEBUG", "Fjernede list_symbols fra active tools (trust-block aktiv)", "")

    if section_instr:
        task_prompt = f"{reason_block}{section_instr}{criteria_block}{sibling_block}{plan_block}{_group_block}{_symbols_block}{_trust_block}{phase_block}\n\nKontekst / Context: {clean_prompt}{chunk_hint}"
    else:
        task_prompt = f"{reason_block}{task_node.name}{criteria_block}{sibling_block}{plan_block}{_group_block}{_symbols_block}{_trust_block}{phase_block}\n\nKontekst / Context: {clean_prompt}{chunk_hint}"

    # Append phase todos as a numbered checklist — LLM must follow the order
    todos = getattr(agent, '_phase_todos', None)
    if todos:
        todo_lines = []
        for i, todo in enumerate(todos, 1):
            status = " [✓]" if todo.get("done") else ""
            todo_lines.append(f"  {i}. {todo.get('text', '')}{status}")
        if todo_lines:
            task_prompt += f"\n\n## {t(K.TODO_AGENT_HEADER, agent.lang)}\n" + "\n".join(todo_lines) + \
                           f"\n\n{t(K.TODO_ORDER_INSTRUCTION, agent.lang)}"

    # Append LLM's own todos (personal checklist)
    llm_todos = getattr(agent, '_llm_todos', None)
    if llm_todos:
        llm_todo_lines = []
        for todo in llm_todos:
            status = " [✓]" if todo.get("done") else ""
            tid = todo.get("id", "")
            llm_todo_lines.append(f"  [{status}] `{tid}` {todo.get('text', '')}")
        if llm_todo_lines:
            task_prompt += f"\n\n## {t(K.TODO_LLM_HEADER, agent.lang)}\n" + "\n".join(llm_todo_lines) + \
                           f"\n\nBrug **update_todo(todo_id='lt_xxx', done=true)** for at markere fremdrift."

    agent._refresh_skills()
    agent._match_skills(clean_prompt)
    skills_block = agent._format_skills_for_prompt()
    if skills_block:
        task_prompt = skills_block + task_prompt
        agent._log("SKILL", "Skills injectet i prompt", skills_block[:200])

    system_prompt = agent.tool_registry.build_system_prompt(task_prompt)
    agent._log("DEBUG", f"file_chunks keys: {list(agent.file_chunks.keys())}", "")
    agent._log("DEBUG", f"clean_prompt length: {len(clean_prompt)}", f"starts with: {clean_prompt[:100]}")
    agent._log("DEBUG", f"system_prompt length: {len(system_prompt)}", f"contains file content: {'###' in system_prompt}")

    # Build user guidance — the "call plan_phase" instruction goes FIRST
    tools_list = ', '.join([k for k in agent.tool_registry.tools if agent.tool_registry.active_tools is None or k in agent.tool_registry.active_tools])
    lang_instr = t(K.ANSWER_IN, agent.lang)
    user_guidance = f"{lang_instr}. "
    # Prominent instruction to build a plan (BEFORE tool guidance)
    if "plan_phase" in getattr(agent.tool_registry, 'active_tools', []) or getattr(agent.tool_registry, 'active_tools', None) is None:
        user_guidance += t(K.TODO_PLAN_START, agent.lang) + " "
    if chunk_hint:
        user_guidance += chunk_hint.strip() + " "
    if tools_list:
        if _use_native_tools(agent):
            user_guidance += t(K.TOOL_CONTINUATION_NATIVE, agent.lang).format(tools_list=tools_list)
        else:
            user_guidance += t(K.TOOL_CONTINUATION, agent.lang).format(tools_list=tools_list, TOOL_MARKER=agent.tool_registry.TOOL_MARKER, DONE_MARKER=agent.tool_registry.DONE_MARKER)
    else:
        user_guidance += t(K.DONE_CONTINUATION, agent.lang).format(DONE_MARKER=agent.tool_registry.DONE_MARKER)
    if not chunk_hint and tools_list:
        has_any_write = any(t in ('write_file', 'edit_file', 'delete_file', 'extract_symbol', 'remove_symbol', 'add_import', 'add_method', 'add_function') for t in agent.tool_registry.active_tools or [])
        if not has_any_write and not agent.images and not agent.file_chunks:
            user_guidance += "\n\nOBS: Ingen filer er indl\u00e6st. Du KAN svare direkte uden at kalde v\u00e6rkt\u00f8jer f\u00f8rst. Sp\u00f8rg IKKE efter filnavne \u2014 brug din egen viden til at besvare opgaven."
    WRITE_TOOLS = {'write_file', 'edit_file', 'delete_file', 'add_method', 'add_function', 'extract_symbol', 'remove_symbol', 'add_import'}
    has_write = any(t in WRITE_TOOLS for t in (agent.tool_registry.active_tools or []))
    if has_write:
        user_guidance += t(K.WRITE_REQUIRED, agent.lang)
        active_write = [t for t in WRITE_TOOLS if t in (agent.tool_registry.active_tools or [])]
        user_guidance += f" Tilg\u00e6ngelige skrivev\u00e6rkt\u00f8jer: {', '.join(active_write)}."
    wta_tip = agent._seq.generate_tool_tip(agent.active_template or "fri", task_node.name) if hasattr(agent, '_seq') else ""
    if wta_tip:
        user_guidance += "\n\n" + wta_tip

    # Tool-specific hints — filtered by active tools to avoid confusing the LLM
    active_tool_set = set(agent.tool_registry.active_tools or [])
    tool_hints = {
        "list_symbols": "\n  Brug list_symbols(filepath='fil.py') for at se ALLE symboler i en Python-fil — gør det FØR locate/read_location når du ikke kender symbolnavnene.",
        "read_chunk": "\n  Read_chunk må KUN bruges til IKKE-PYTHON filer (JSON, HTML, TXT, osv.). For .py-filer, brug read_location i stedet.",
        "locate": "\n  Brug locate(name='funktionsnavn') for at finde en PYTHON funktion/klasse/variabel på tværs af ALLE .py-filer. name er et Python symbol (def/class/variable), IKKE et værktøjsnavn (tool). locate returnerer også 'also_in_file'.",
        "read_location": "\n  Brug read_location(filepath='fil.py', name='funktionsnavn') for at læse KUN en bestemt funktion/metode/klasse — IKKE hele filen.",
        "write_file": "\n  Brug write_file(path='ny_fil.py', content='...') for at oprette NYE filer der IKKE findes i forvejen. Brug ALDRIG write_file til at erstatte eksisterende filer — brug edit_file i stedet.",
        "edit_file": "\n  Brug edit_file(path='fil.py', old_text='tekst der skal erstattes', new_text='ny tekst') for at redigere EKSISTERENDE filer. Læs filen FØRST med read_chunk, kopier den præcise tekst som old_text. For at TILFØJE en linje: sæt old_text = hele filens indhold, og new_text = det gamle indhold + den nye linje. ERSTAT ALDRIG hele indholdet med kun den nye tekst.",
        "add_method": "\n  Brug add_method(filepath='fil.py', class_name='MinKlasse', method_code='def ny_metode(self):\\n    pass') for at TILFØJE en ny metode til en eksisterende klasse. Du skal KUN angive den nye metodekode — IKKE hele klassen. Dette undgår escaping-problemer med edit_file.",
        "add_function": "\n  Brug add_function(filepath='fil.py', function_code='def ny_funktion():\\n    pass') for at TILFØJE en ny module-level funktion. Valgfrit: after_symbol='anden_funk' indsætter efter givet symbol.",
        "delete_file": "\n  Brug delete_file(filepath='overflødig_fil.py') for at SLETTE en hel fil der ikke længere er nødvendig. Bekræft ALTID at filen ikke bruges af anden kode før sletning.",
        "run_tests": "\n  Brug run_tests() for at køre tests og verificere at din kode virker.",
        "update_issue_status": "\n  Brug update_issue_status(issue_id='...', status='resolved') når et issue er løst.",
    }
    filtered_hints = [h for tool_name, h in tool_hints.items() if tool_name in active_tool_set]
    if filtered_hints:
        user_guidance += "\n\n## VÆRKTØJSGUIDE" + "".join(filtered_hints)

    messages = [{"role": "system", "content": system_prompt}]
    if file_ctx:
        messages.append({"role": "system", "content": f"## Filindhold (f\u00f8rste iteration)\n\n{file_ctx}"})
        # Append structured entity map if available
        _em = getattr(agent, '_entity_map', None)
        if _em:
            try:
                import agent_entity_map
                _em_text = agent_entity_map.format_entity_map_prompt(_em)
                if _em_text:
                    messages.append({"role": "system", "content": _em_text})
            except Exception:
                pass
        extraction_guidance = t(K.EXTRACT_CONTEXT_FIRST, agent.lang)
        user_guidance = extraction_guidance + "\n\n" + user_guidance
    messages.append({"role": "user", "content": user_guidance})
    agent._log("LLM", "System prompt", f"{len(system_prompt)} chars \u2014 {system_prompt[:300]}...")
    agent._log("LLM", "User guidance", user_guidance)
    return messages, tools_list, bool(file_ctx)



def _build_chunk_hint(agent: Any) -> str:
    """build chunk hint.
    
    Args:
        agent:"""
    available_keys = list(agent.file_chunks.keys())
    hint = ""
    if available_keys:
        parts = []
        for key in available_keys:
            total = len(agent.file_chunks[key])
            display = key.replace("file_", "", 1)
            parts.append(f"\n  {display} ({total} chunk{'s' if total > 1 else ''})")
        base_dir = os.environ.get("AGENT_WORKDIR", "") or os.path.abspath('.')
        hint = f"\n\n## TILG\u00c6NGELIGE FILER (projektmappe: {base_dir})"
        hint += "".join(parts)
        hint += "\n\n  Brug list_symbols(filepath='fil.py') for at se ALLE symboler (funktioner, klasser, variabler) i en Python-fil — g\u00f8r det F\u00d8R locate/read_location n\u00e5r du ikke kender symbolnavnene."
        hint += "\n  Brug locate(name='funktionsnavn') for at finde en funktion p\u00e5 tv\u00e6rs af ALLE .py-filer (filepath er valgfri)."
        hint += "\n  locate returnerer ogs\u00e5 en 'also_in_file'-liste over andre symboler i filen — brug locate til hver enkelt."
        hint += "\n  Brug read_location(filepath='fil.py', name='funktionsnavn') for at l\u00e6se KUN en bestemt funktion/metode/klasse — IKKE hele filen."
        hint += "\n  Brug IKKE read_chunk til .py-filer — read_location er altid at foretr\u00e6kke og returnerer kun det relevante kode."
        hint += "\n  Read_chunk m\u00e5 KUN bruges til IKKE-PYTHON filer (JSON, HTML, TXT, osv.)."
    delegation_lines = []
    for key, chunks in agent.file_chunks.items():
        content = chunks[0] if chunks else ''
        if not content:
            continue
        for func_name, target_mod in agent_files.detect_delegations(content):
            target_key = f'file_{target_mod}.py'
            if target_key in agent.file_chunks:
                delegation_lines.append(f'  - {key.replace("file_", "", 1)}:{func_name} \u2192 rediger i stedet {target_mod}.py:{func_name}')
            else:
                delegation_lines.append(f'  - {key.replace("file_", "", 1)}:{func_name} \u2192 {target_mod}.py (ikke indl\u00e6st)')
    if delegation_lines:
        hint += '\n\n## DELEGERINGER\nNogle funktioner i de indl\u00e6ste filer er stubs, der kun videresender til en anden fil.\n' + '\n'.join(delegation_lines) + '\n'
    return hint



def _build_phase_reason(template: str, phase_name: str, original_prompt: str) -> str:
    """Build an 'I'm working on X for Y. They need Z. With that:' reason block.

    Gives the LLM context about WHY this phase exists, not just WHAT to do.
    The pattern helps the LLM understand the purpose and act more intelligently.
    """
    phase = _normalize_phase(phase_name).lower()
    file_match = re.search(r"([a-zA-Z_][\w.]+\.py)", original_prompt or "")
    target_file = file_match.group(1) if file_match else "koden"

    reasons = {
        ("refactor", "analyse"): (
            f"Jeg arbejder p\u00e5 at opdele {target_file} i mindre moduler. "
            f"Brugeren har brug for at forst\u00e5 symbolstrukturen og afh\u00e6ngighederne "
            f"f\u00f8r modulopdeling."
        ),
        ("refactor", "plan"): (
            f"Jeg har nu overblik over {target_file}. "
            f"Brugeren har brug for en konkret plan for hvilke moduler der skal oprettes."
        ),
        ("refactor", "ekstraher"): (
            f"Planen er klar. "
            f"Brugeren har brug for at symboler flyttes fra {target_file} til nye modulfiler."
        ),
        ("refactor", "opdat\u00e9r"): (
            f"Modulerne er oprettet. "
            f"Brugeren har brug for at {target_file} opryddes "
            f"\u2014 fjern flyttede symboler og tilf\u00f8j imports."
        ),
        ("refactor", "test"): (
            f"Refaktoreringen er udf\u00f8rt. "
            f"Brugeren har brug for at verificere at alle tests stadig best\u00e5r."
        ),
        ("bugfix", "analyse"): (
            f"Jeg unders\u00f8ger en bugrapport. "
            f"Brugeren har brug for at forst\u00e5 hvor fejlen opst\u00e5r."
        ),
        ("bugfix", "test"): (
            f"Jeg har forst\u00e5et fejlen. "
            f"Brugeren har brug for en test der reproducerer den."
        ),
        ("bugfix", "implementering"): (
            f"Testen bekr\u00e6fter fejlen. "
            f"Brugeren har brug for en minimal rettelse."
        ),
        ("bugfix", "verifikation"): (
            f"Rettelsen er anvendt. "
            f"Brugeren har brug for at bekr\u00e6fte at alle tests best\u00e5r."
        ),
        ("bugfix", "opdatering"): (
            f"Alt virker. "
            f"Brugeren har brug for at issuet markeres som l\u00f8st."
        ),
        ("selvforbedring", "analyser"): (
            f"Jeg unders\u00f8ger hvorfor en fase fejlede. "
            f"Brugeren har brug for at forst\u00e5 fejlkonteksten."
        ),
        ("selvforbedring", "diagnostic\u00e9r"): (
            f"Jeg har overblikket. "
            f"Brugeren har brug for at identificere rod\u00e5rsagen."
        ),
        ("selvforbedring", "ret"): (
            f"Rod\u00e5rsagen er kendt. "
            f"Brugeren har brug for at koden rettes."
        ),
        ("selvforbedring", "verific\u00e9r"): (
            f"Rettelsen er anvendt. "
            f"Brugeren har brug for at tests k\u00f8rer og issuet lukkes."
        ),
        ("selvforbedring", "commit"): (
            f"Alt er verificeret. "
            f"Brugeren har brug for at \u00e6ndringerne committes."
        ),
        ("issue_handler", "l\u00e6s"): (
            f"Jeg har f\u00e5et et issue. "
            f"Brugeren har brug for at forst\u00e5 hvad der skal laves."
        ),
        ("issue_handler", "afklar"): (
            f"Jeg har l\u00e6st issuet. "
            f"Brugeren har brug for at afklare pr\u00e6cis hvad der skal \u00e6ndres."
        ),
        ("issue_handler", "fix"): (
            f"Jeg ved hvad der skal laves. "
            f"Brugeren har brug for at koden rettes og testes."
        ),
        ("issue_handler", "luk"): (
            f"Fikset er implementeret. "
            f"Brugeren har brug for at issuet markeres som l\u00f8st."
        ),
    }

    key = (template or "").lower(), phase
    if key in reasons:
        return f"## Baggrund\n{reasons[key]}\n"
    return ""



def _cont_hint(agent: Any, tools_list: str) -> str:
    """cont hint.
    
    Args:
        agent:
        tools_list:"""
    if _use_native_tools(agent):
        return t(K.TOOL_CONTINUATION_NATIVE, agent.lang).format(tools_list=tools_list)
    return t(K.TOOL_CONTINUATION, agent.lang).format(tools_list=tools_list, TOOL_MARKER=agent.tool_registry.TOOL_MARKER, DONE_MARKER=agent.tool_registry.DONE_MARKER)



def _add_user_msg(messages: list[dict], content: str) -> None:
    """add user msg.
    
    Args:
        messages:
        content:"""
    messages.append({"role": "user", "content": content})



def _msg_content_len(m: dict) -> int:
    """msg content len.
    
    Args:
        m:"""
    c = m.get("content", "")
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        return sum(p.get("text", "").__len__() if isinstance(p, dict) and p.get("type") == "text" else 0 for p in c)
    return 0



def _truncate_messages(messages: list[dict], max_chars: int, agent: Any | None = None) -> list[dict]:
    """truncate messages.

    When truncating for a refactor template, saves the full conversation to a temp file
    and injects a compact progress summary so the LLM retains awareness of work done.

    Args:
        messages:
        max_chars:
        agent:"""
    total = sum(_msg_content_len(m) for m in messages)
    if total <= max_chars or len(messages) <= 3:
        return messages
    mid = "\n[... tidligere kontekst afkortet ...]"
    system = [m for m in messages if m["role"] == "system"]
    non_system = [m for m in messages if m["role"] != "system"]

    # For refactor template: save full context and build progress summary
    is_refactor = agent and getattr(agent, 'active_template', '') == 'refactor'
    if is_refactor and agent:
        _save_full_context_for_refactor(agent, messages)
        summary = _build_truncation_summary(messages, agent)
        mid += "\n\n" + summary

    keep_pairs = 6 if is_refactor else 4
    tail = non_system[-keep_pairs:] if len(non_system) > keep_pairs else non_system
    # Ensure tail doesn't start with a bare "tool" message (LM Studio template
    # error: "Message has tool role, but no preceding assistant with tool_calls")
    if tail and tail[0].get("role") == "tool":
        # Walk back to include the preceding assistant message so the pair is complete
        cut_point = len(non_system) - keep_pairs
        if cut_point > 0:
            for i in range(cut_point - 1, -1, -1):
                if non_system[i].get("role") == "assistant":
                    tail = non_system[i:]
                    break
    insert = [{"role": "user", "content": mid}]
    return system + insert + tail



def _build_truncation_summary(messages: list[dict], agent: Any) -> str:
    """Build a compact summary of work done from the full message history.

    Extracts tool calls and their outcomes so the LLM knows what has been accomplished
    even after truncation removes earlier messages.
    """
    lines = []
    tools_summary: dict[str, list] = {}
    symbols_moved: list[str] = []
    modules_created: set[str] = set()

    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if not isinstance(content, str):
            continue

        # Extract tool calls from assistant messages with tool_calls
        if role == "assistant" and "tool_calls" in content.lower():
            pass  # handled via tool results below

        # Extract tool results
        if role == "tool":
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    result_data = parsed.get("result", parsed)
                    if isinstance(result_data, dict):
                        inner = result_data.get("result", result_data)
                        # batch_extract_symbols results
                        if isinstance(inner, dict) and "total" in inner:
                            target = inner.get("target", "?")
                            succeeded = inner.get("succeeded", 0)
                            symbols_in_batch = []
                            for r in inner.get("results", []):
                                sym = r.get("symbol", "")
                                if sym:
                                    symbols_moved.append(sym)
                                    symbols_in_batch.append(sym)
                            modules_created.add(os.path.basename(target))
                            tools_summary.setdefault("batch_extract_symbols", []).append(
                                f"✅ {succeeded} symbols → {os.path.basename(target)}"
                            )
                        # extract_symbol results
                        elif isinstance(inner, dict) and "symbol" in inner:
                            sym = inner.get("symbol", "")
                            target = inner.get("target", "?")
                            if sym and inner.get("success"):
                                symbols_moved.append(sym)
                                modules_created.add(os.path.basename(target))
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

    # Count remaining symbols in source file
    remaining_count = ""
    try:
        import agent_files as _af
        _src = _resolve_source_file(agent, getattr(agent, 'original_prompt', ''))
        result = _af.list_symbols(_src)
        if isinstance(result, dict) and result.get("success"):
            symbols = result.get("symbols", [])
            count = len(symbols) if isinstance(symbols, list) else 0
            remaining_count = f"{_src}: {count} symbols tilbage"
    except Exception:
        pass

    # Build summary lines — always include remaining count
    if symbols_moved:
        unique_symbols = list(dict.fromkeys(symbols_moved))
        lines.append(f"Fremgang: {len(unique_symbols)} symboler flyttet til {len(modules_created)} modul(er): {', '.join(sorted(modules_created))}")
    if remaining_count:
        lines.append(remaining_count)

    if tools_summary:
        for tool, entries in tools_summary.items():
            recent = entries[-3:]  # last 3 batches
            lines.append(f"Seneste {tool} kald: {' | '.join(recent)}")

    return "\n".join(lines) if lines else ""



def _extract_last_assistant_text(messages: list[dict]) -> str:
    """Extract the last substantive assistant text from messages, skipping tool-only responses."""
    if not messages:
        return ""
    for m in reversed(messages):
        if m["role"] != "assistant":
            continue
        content = m.get("content")
        if not content:
            continue
        text = content if isinstance(content, str) else ""
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    break
        text = text.strip()
        if len(text) > 50:
            return text
    return ""
