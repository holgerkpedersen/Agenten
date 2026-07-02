from typing import Any, Generator
import os
from lang import t
import json
from i18n import K

_TEXTS: dict[str, dict[str, str]] = {
    # bugfix — analyse
    "bf_a1": {"da": "Læs issue med read_issue()", "en": "Read issue with read_issue()", "es": "Lee issue con read_issue()", "zh": "使用 read_issue() 读取问题"},
    "bf_a2": {"da": "Find relevant kode med locate()", "en": "Find relevant code with locate()", "es": "Encuentra código relevante con locate()", "zh": "使用 locate() 查找相关代码"},
    "bf_a3": {"da": "Sammenlign koden med buggens påstand", "en": "Compare the code with the bug claim", "es": "Compara el código con la afirmación del bug", "zh": "将代码与错误声明进行比较"},
    "bf_a4": {"da": "Afgør om fejlen findes eller er rettet", "en": "Determine if the bug exists or is already fixed", "es": "Determina si el error existe o ya está corregido", "zh": "确定错误是否存在或已修复"},
    "bf_t1": {"da": "Opret testfil i tests/temp/ med write_file", "en": "Create test file in tests/temp/ with write_file", "es": "Crea archivo de prueba en tests/temp/ con write_file", "zh": "使用 write_file 在 tests/temp/ 中创建测试文件"},
    "bf_t2": {"da": "Kør specifik test - den SKAL fejle (rød fase)", "en": "Run specific test - it MUST fail (red phase)", "es": "Ejecuta prueba específica - DEBE fallar (fase roja)", "zh": "运行特定测试 - 必须失败（红色阶段）"},
    "bf_i1": {"da": "Ret kildekoden med edit_file/add_method/add_function", "en": "Fix source code with edit_file/add_method/add_function", "es": "Corrige el código fuente con edit_file/add_method/add_function", "zh": "使用 edit_file/add_method/add_function 修复源代码"},
    "bf_i2": {"da": "Undgå write_file - filen findes allerede", "en": "Avoid write_file - file already exists", "es": "Evita write_file - el archivo ya existe", "zh": "避免 write_file - 文件已存在"},
    "bf_i3": {"da": "Brug add_method til nye metoder i klasse", "en": "Use add_method for new methods in class", "es": "Usa add_method para nuevos métodos en clase", "zh": "使用 add_method 添加类中的新方法"},
    "bf_v1": {"da": "Kør specifik test - den SKAL bestå (grøn fase)", "en": "Run specific test - it MUST pass (green phase)", "es": "Ejecuta prueba específica - DEBE pasar (fase verde)", "zh": "运行特定测试 - 必须通过（绿色阶段）"},
    "bf_v2": {"da": "Kør HELE testsuiten for at tjekke regression", "en": "Run the ENTIRE test suite to check regression", "es": "Ejecuta TODA la suite de pruebas para verificar regresión", "zh": "运行整个测试套件以检查回归"},
    "bf_o1": {"da": "Opdater issue status til 'resolved'", "en": "Update issue status to 'resolved'", "es": "Actualiza el estado del issue a 'resolved'", "zh": "将问题状态更新为 'resolved'"},
    # refactor — analyse
    "rf_a_read": {"da": "Læs issue med read_issue()", "en": "Read issue with read_issue()", "es": "Leer issue con read_issue()", "zh": "使用 read_issue() 读取问题"},
    "rf_a_locate": {"da": "Find relevant kode i api_server.py", "en": "Find relevant code in api_server.py", "es": "Encuentra código relevante en api_server.py", "zh": "在 api_server.py 中查找相关代码"},
    "rf_a_chunks": {"da": "List chunks med list_chunks()", "en": "List chunks with list_chunks()", "es": "Lista chunks con list_chunks()", "zh": "使用 list_chunks() 列出代码块"},
    "rf_a_plan": {"da": "Udforsk og forstå koden", "en": "Explore and understand the code", "es": "Explora y comprende el código", "zh": "探索并理解代码"},
    # refactor — plan
    "rf_p_symbols": {"da": "List symboler med list_symbols()", "en": "List symbols with list_symbols()", "es": "Lista símbolos con list_symbols()", "zh": "使用 list_symbols() 列出符号"},
    "rf_p_plan": {"da": "Opret refactor_plan.md med write_file()", "en": "Create refactor_plan.md with write_file()", "es": "Crea refactor_plan.md con write_file()", "zh": "使用 write_file() 创建 refactor_plan.md"},
    # refactor — ekstraher
    "rf_e_list": {"da": "List symboler i {} med list_symbols()", "en": "List symbols in {} with list_symbols()", "es": "Lista símbolos en {} con list_symbols()", "zh": "使用 list_symbols() 列出 {} 中的符号"},
    "rf_e_verify": {"da": "Verificer syntaks med verify_refactor()", "en": "Verify syntax with verify_refactor()", "es": "Verifica sintaxis con verify_refactor()", "zh": "使用 verify_refactor() 验证语法"},
    "rf_e_tests": {"da": "Kør tests for at bekræfte ingen regression", "en": "Run tests to confirm no regression", "es": "Ejecuta pruebas para confirmar que no hay regresión", "zh": "运行测试以确认无回归"},
    "rf_e_status": {"da": "Markér REFAC som resolved med update_issue_status()", "en": "Mark REFAC as resolved with update_issue_status()", "es": "Marca REFAC como resuelto con update_issue_status()", "zh": "使用 update_issue_status() 将 REFAC 标记为已解决"},
    # refactor — opdatér
    "rf_u_find": {"da": "Find symboler der skal opdateres med list_symbols()", "en": "Find symbols to update with list_symbols()", "es": "Encuentra símbolos para actualizar con list_symbols()", "zh": "使用 list_symbols() 查找要更新的符号"},
    "rf_u_import": {"da": "Tilføj import fra {} i {}", "en": "Add import from {} in {}", "es": "Añade importación desde {} en {}", "zh": "从 {} 添加导入到 {}"},
    "rf_u_verify": {"da": "Verificer syntaks med verify_refactor()", "en": "Verify syntax with verify_refactor()", "es": "Verifica sintaxis con verify_refactor()", "zh": "使用 verify_refactor() 验证语法"},
    "rf_u_tests": {"da": "Kør tests for at bekræfte ingen regression", "en": "Run tests to confirm no regression", "es": "Ejecuta pruebas para confirmar que no hay regresión", "zh": "运行测试以确认无回归"},
    "rf_u_status": {"da": "Markér REFAC som resolved med update_issue_status()", "en": "Mark REFAC as resolved with update_issue_status()", "es": "Marca REFAC como resuelto con update_issue_status()", "zh": "使用 update_issue_status() 将 REFAC 标记为已解决"},
    # refactor — test
    "rf_t_tests": {"da": "Kør alle tests for at bekræfte ingen regression", "en": "Run all tests to confirm no regression", "es": "Ejecuta todas las pruebas para confirmar que no hay regresión", "zh": "运行所有测试以确认无回归"},
    "rf_t_status": {"da": "Opdater issue status til 'resolved'", "en": "Update issue status to 'resolved'", "es": "Actualiza el estado del issue a 'resolved'", "zh": "将问题状态更新为 'resolved'"},
    # kodeanalyse
    "ka_a1": {"da": "List symboler med list_symbols()", "en": "List symbols with list_symbols()", "es": "Lista símbolos con list_symbols()", "zh": "使用 list_symbols() 列出符号"},
    "ka_a2": {"da": "Læs vigtige funktioner med read_location()", "en": "Read important functions with read_location()", "es": "Lee funciones importantes con read_location()", "zh": "使用 read_location() 读取重要函数"},
    "ka_a3": {"da": "Analyser afhængigheder", "en": "Analyze dependencies", "es": "Analiza dependencias", "zh": "分析依赖关系"},
    "ka_a4": {"da": "Skriv analyserapport med write_file()", "en": "Write analysis report with write_file()", "es": "Escribe informe de análisis con write_file()", "zh": "使用 write_file() 编写分析报告"},
    "ka_i1": {"da": "Gennemgå imports med read_location()", "en": "Review imports with read_location()", "es": "Revisa importaciones con read_location()", "zh": "使用 read_location() 审查导入"},
    "ka_i2": {"da": "Notér ubrugte og cirkulære imports", "en": "Note unused and circular imports", "es": "Anota importaciones no utilizadas y circulares", "zh": "记录未使用和循环导入"},
    "ka_i3": {"da": "Skriv import-rapport med write_file()", "en": "Write import report with write_file()", "es": "Escribe informe de importaciones con write_file()", "zh": "使用 write_file() 编写导入报告"},
    "ka_k1": {"da": "Analysér klasse- og funktionsstruktur", "en": "Analyze class and function structure", "es": "Analiza estructura de clases y funciones", "zh": "分析类和函数结构"},
    "ka_k2": {"da": "Vurdér design patterns og SOLID", "en": "Evaluate design patterns and SOLID", "es": "Evalúa patrones de diseño y SOLID", "zh": "评估设计模式和 SOLID 原则"},
    "ka_k3": {"da": "Skriv arkitektur-rapport med write_file()", "en": "Write architecture report with write_file()", "es": "Escribe informe de arquitectura con write_file()", "zh": "使用 write_file() 编写架构报告"},
    "ka_q1": {"da": "Vurdér læsbarhed, navngivning, type hints", "en": "Evaluate readability, naming, type hints", "es": "Evalúa legibilidad, nombres, type hints", "zh": "评估可读性、命名、类型提示"},
    "ka_q2": {"da": "Tjek test coverage og kompleksitet", "en": "Check test coverage and complexity", "es": "Verifica cobertura de pruebas y complejidad", "zh": "检查测试覆盖率和复杂度"},
    "ka_q3": {"da": "Skriv kodekvalitets-rapport med write_file()", "en": "Write code quality report with write_file()", "es": "Escribe informe de calidad de código con write_file()", "zh": "使用 write_file() 编写代码质量报告"},
    "ka_s1": {"da": "Analysér inputvalidering og autentifikation", "en": "Analyze input validation and authentication", "es": "Analiza validación de entrada y autenticación", "zh": "分析输入验证和身份验证"},
    "ka_s2": {"da": "Tjek for OWASP-top-10 sårbarheder", "en": "Check for OWASP Top 10 vulnerabilities", "es": "Verifica vulnerabilidades OWASP Top 10", "zh": "检查 OWASP Top 10 漏洞"},
    "ka_s3": {"da": "Skriv sikkerheds-rapport med write_file()", "en": "Write security report with write_file()", "es": "Escribe informe de seguridad con write_file()", "zh": "使用 write_file() 编写安全报告"},
    # programmering
    "pr_a1": {"da": "Analysér krav og behov", "en": "Analyze requirements and needs", "es": "Analiza requisitos y necesidades", "zh": "分析需求"},
    "pr_a2": {"da": "Skriv kravanalyse i docs/kravanalyse.md", "en": "Write requirements analysis in docs/kravanalyse.md", "es": "Escribe análisis de requisitos en docs/kravanalyse.md", "zh": "将需求分析写入 docs/kravanalyse.md"},
    "pr_d1": {"da": "Design arkitektur med komponenter og grænseflader", "en": "Design architecture with components and interfaces", "es": "Diseña arquitectura con componentes e interfaces", "zh": "设计包含组件和接口的架构"},
    "pr_d2": {"da": "Skriv arkitektur i docs/arkitektur.md", "en": "Write architecture in docs/arkitektur.md", "es": "Escribe arquitectura en docs/arkitektur.md", "zh": "将架构写入 docs/arkitektur.md"},
    "pr_p1": {"da": "Lav implementeringsplan med moduler og rækkefølge", "en": "Create implementation plan with modules and order", "es": "Crea plan de implementación con módulos y orden", "zh": "创建包含模块和顺序的实施计划"},
    "pr_p2": {"da": "Skriv plan i docs/implementeringsplan.md", "en": "Write plan in docs/implementeringsplan.md", "es": "Escribe plan en docs/implementeringsplan.md", "zh": "将计划写入 docs/implementeringsplan.md"},
    "pr_s1": {"da": "Analysér sikkerhedsaspekter", "en": "Analyze security aspects", "es": "Analiza aspectos de seguridad", "zh": "分析安全方面"},
    "pr_s2": {"da": "Skriv sikkerhedsanalyse i docs/sikkerhedsanalyse.md", "en": "Write security analysis in docs/sikkerhedsanalyse.md", "es": "Escribe análisis de seguridad en docs/sikkerhedsanalyse.md", "zh": "将安全分析写入 docs/sikkerhedsanalyse.md"},
    "pr_r1": {"da": "Udfyld detaljer og præcisér specifikationer", "en": "Fill in details and clarify specifications", "es": "Completa detalles y clarifica especificaciones", "zh": "填充细节并明确规范"},
    "pr_i1": {"da": "Implementér koden med write_file()", "en": "Implement the code with write_file()", "es": "Implementa el código con write_file()", "zh": "使用 write_file() 实现代码"},
    "pr_i2": {"da": "Kør tests med run_tests()", "en": "Run tests with run_tests()", "es": "Ejecuta pruebas con run_tests()", "zh": "使用 run_tests() 运行测试"},
    # issue_handler
    "ih_a1": {"da": "Læs issue med read_issue()", "en": "Read issue with read_issue()", "es": "Lee issue con read_issue()", "zh": "使用 read_issue() 读取问题"},
    "ih_a2": {"da": "Find relevant kode med locate()", "en": "Find relevant code with locate()", "es": "Encuentra código relevante con locate()", "zh": "使用 locate() 查找相关代码"},
    "ih_c1": {"da": "Forstå problemet og afgør løsning", "en": "Understand the problem and decide solution", "es": "Comprende el problema y decide la solución", "zh": "理解问题并决定解决方案"},
    "ih_f1": {"da": "Ret koden med edit_file()", "en": "Fix the code with edit_file()", "es": "Corrige el código con edit_file()", "zh": "使用 edit_file() 修复代码"},
    "ih_f2": {"da": "Kør tests med run_tests()", "en": "Run tests with run_tests()", "es": "Ejecuta pruebas con run_tests()", "zh": "使用 run_tests() 运行测试"},
    "ih_l1": {"da": "Opdater issue status til 'resolved'", "en": "Update issue status to 'resolved'", "es": "Actualiza el estado del issue a 'resolved'", "zh": "将问题状态更新为 'resolved'"},
    # selvforbedring
    "sf_a1": {"da": "Læs CORE-issue med read_issue()", "en": "Read CORE issue with read_issue()", "es": "Lee issue CORE con read_issue()", "zh": "使用 read_issue() 读取 CORE 问题"},
    "sf_a2": {"da": "Find relevant kode med locate()", "en": "Find relevant code with locate()", "es": "Encuentra código relevante con locate()", "zh": "使用 locate() 查找相关代码"},
    "sf_d1": {"da": "Kør tests med run_tests()", "en": "Run tests with run_tests()", "es": "Ejecuta pruebas con run_tests()", "zh": "使用 run_tests() 运行测试"},
    "sf_d2": {"da": "Identificér rodårsag", "en": "Identify root cause", "es": "Identifica la causa raíz", "zh": "识别根本原因"},
    "sf_r1": {"da": "Ret koden med edit_file()", "en": "Fix the code with edit_file()", "es": "Corrige el código con edit_file()", "zh": "使用 edit_file() 修复代码"},
    "sf_r2": {"da": "Kør tests for at bekræfte fix", "en": "Run tests to confirm fix", "es": "Ejecuta pruebas para confirmar la corrección", "zh": "运行测试以确认修复"},
    "sf_v1": {"da": "Kør HELE testsuiten", "en": "Run the ENTIRE test suite", "es": "Ejecuta TODA la suite de pruebas", "zh": "运行整个测试套件"},
    "sf_v2": {"da": "Opdater CORE-issue status til 'resolved'", "en": "Update CORE issue status to 'resolved'", "es": "Actualiza el estado del issue CORE a 'resolved'", "zh": "将 CORE 问题状态更新为 'resolved'"},
    "sf_c1": {"da": "Commit ændringer med git_commit()", "en": "Commit changes with git_commit()", "es": "Confirma cambios con git_commit()", "zh": "使用 git_commit() 提交更改"},
    # testgenerering
    "tg_a1": {"da": "Analysér koden og find testbare enheder", "en": "Analyze the code and find testable units", "es": "Analiza el código y encuentra unidades comprobables", "zh": "分析代码并找到可测试的单元"},
    "tg_t1": {"da": "Opret testfil i tests/temp/ med write_file()", "en": "Create test file in tests/temp/ with write_file()", "es": "Crea archivo de prueba en tests/temp/ con write_file()", "zh": "使用 write_file() 在 tests/temp/ 中创建测试文件"},
    "tg_t2": {"da": "Kør test - den SKAL fejle først (rød)", "en": "Run test - it MUST fail first (red)", "es": "Ejecuta prueba - DEBE fallar primero (rojo)", "zh": "运行测试 - 必须先失败（红色）"},
    "tg_i1": {"da": "Implementér koden der gør testen grøn", "en": "Implement the code that makes the test pass (green)", "es": "Implementa el código que hace pasar la prueba (verde)", "zh": "实现使测试通过的代码（绿色）"},
    "tg_i2": {"da": "Kør specifik test - skal bestå", "en": "Run specific test - must pass", "es": "Ejecuta prueba específica - debe pasar", "zh": "运行特定测试 - 必须通过"},
    "tg_v1": {"da": "Kør HELE testsuiten for regression", "en": "Run the ENTIRE test suite for regression", "es": "Ejecuta TODA la suite de pruebas para regresión", "zh": "运行整个测试套件以检查回归"},
    # agenten
    "ag_b1": {"da": "Opret og skift til ny branch", "en": "Create and switch to new branch", "es": "Crea y cambia a nueva rama", "zh": "创建并切换到新分支"},
    "ag_c1": {"da": "Commit ændringer", "en": "Commit changes", "es": "Confirma cambios", "zh": "提交更改"},
    "ag_p1": {"da": "Push til remote", "en": "Push to remote", "es": "Sube a remoto", "zh": "推送到远程"},
    "ag_pr1": {"da": "Opret Pull Request", "en": "Create Pull Request", "es": "Crea Pull Request", "zh": "创建 Pull Request"},
    # generic
    "generic_phase": {"da": "Gennemfør fasen: {}", "en": "Complete the phase: {}", "es": "Completa la fase: {}", "zh": "完成阶段: {}"},
    "verify_criteria": {"da": "Verificér at fasens succeskriterier er opfyldt", "en": "Verify that the phase success criteria are met", "es": "Verifica que se cumplan los criterios de éxito de la fase", "zh": "验证阶段成功标准是否已满足"},
}

def _todo_text(key: str, lang: str = "da", fmt: str | None = None) -> str:
    txt = _TEXTS.get(key, {}).get(lang) or _TEXTS.get(key, {}).get("en") or key
    if fmt is not None:
        return txt.format(fmt)
    return txt
import agent_files
import agent_phase_checks
from agent_refactor_helpers import _resolve_refactor_plan_path, _check_import_placement, _refactor_actually_moved_code, _build_refactor_phase_context, _save_full_context_for_refactor, _count_symbols_in_file, _get_symbol_names_in_file, _validate_ekstraher_symbols, _build_module_progress_msg, _detect_module_deps, _resolve_source_file, _check_refactor_progress, _all_planned_modules_exist
from agent_task_phase import _get_max_iterations, _get_max_tool_calls, _normalize_phase, _set_phase_model, set_task_tools
from agent_utils import _is_greenfield, _use_native_tools, _normalize_phase, _inject_todo_tools

def _get_phase_auto_complete_msg(task_node: Any, tool_name: str, tool_result: dict | Any, agent: Any, called_tools: dict | None = None, full_response: str = "") -> str | None:
    """Return auto-complete message if the phase goal was just met, else None.
    Checks phase-specific success conditions after each tool call.

    Two layers of auto-complete:
      1. Tool-result-based (run_tests passed, update_issue_status succeeded)
      2. Deterministic phase-check (file_exists, files_from_plan, etc.) —
         runs after any tool call when the template defines a check for
         this phase

    Args:
        called_tools: dict of ``"{tool_name}{args_repr}"`` → count. Forwarded
            to ``check_phase_done`` so ``tool_called`` and ``tests_pass``
            checks can see the full call history.
        full_response: accumulated LLM streaming text. Forwarded to
            ``check_phase_done`` for ``min_text_length``.
    """
    task_name = getattr(task_node, "name", "") or ""
    phase = _normalize_phase(task_name).lower()

    # Bloker auto-complete for Analyse og Plan hvis LLM'en ikke har
    # oprettet sin egen opgaveplan endnu (kræver plan_phase kaldt).
    if phase in ("analyse", "plan") and not getattr(agent, '_llm_has_planned', False):
        return None

    if tool_name == "run_tests" and not agent._tests_failed:
        if "test" in phase:
            if getattr(agent, 'active_template', '') == 'refactor':
                if _refactor_actually_moved_code(agent):
                    agent.issue_resolved = True
                    agent._needs_resolve_persist = True
                    return t(K.LOG_REFACTOR_TESTS_PASSED, agent.lang)
                return (
                    t(K.LOG_REFACTOR_TESTS_PASSED, agent.lang)
                    + "\n\n"
                    + t(K.TEST_BUT_NO_REFACTOR, agent.lang)
                )
            # Do NOT auto-resolve for Test (Red) — the LLM must decide whether
            # the bug is already fixed (call update_issue_status) or whether
            # the test asserts buggy behavior (rewrite the test).
            return t(K.LOG_RED_TEST_PASSED_GUIDANCE, agent.lang)
        if any(k in phase for k in ["implementering", "fix", "verifikation",
                                     "opdatering", "luk", "close", "green"]):
            return t(K.LOG_PHASE_COMPLETE, agent.lang)

    if tool_name == "update_issue_status":
        if isinstance(tool_result, dict) and tool_result.get("success"):
            if any(k in phase for k in ["opdatering", "update", "luk", "close"]):
                return t(K.LOG_PHASE_COMPLETE, agent.lang)
            # Bug already fixed — auto-complete implementering/fix phases
            if any(k in phase for k in ["implementering", "fix", "verifikation", "green"]):
                return t(K.LOG_PHASE_COMPLETE, agent.lang)
            # Analyse phase resolved the issue — auto-complete
            if any(k in phase for k in ["analyse", "analysis"]):
                agent.issue_resolved = True
                return t(K.LOG_PHASE_COMPLETE, agent.lang)

    # Phase output verification — prevent auto-complete when no output was produced
    if tool_name in ("write_file",):
        if "plan" in phase:
            plan_path = getattr(agent, '_refactor_plan_path', '') or os.path.join(agent_files._resolve_workdir(), "refactor_plan.md")
            if not os.path.exists(plan_path) or os.path.getsize(plan_path) == 0:
                agent._log("DEBUG", "Plan output verification", f"{plan_path} mangler eller er tom — afslutter IKKE auto-complete")
                return None
        if "ekstraher" in phase:
            # Check if write_file was actually called with a module name (not refactor_plan.md)
            wrote_module = False
            for tool_key in (called_tools or {}):
                if tool_key.startswith("write_file"):
                    try:
                        args_str = tool_key[len("write_file"):]
                        args = json.loads(args_str) if args_str else {}
                        fname = args.get("filepath", "") or args.get("file_path", "") or ""
                        if fname and fname != "refactor_plan.md":
                            wrote_module = True
                            break
                    except (json.JSONDecodeError, ValueError):
                        wrote_module = True
                        break
            if not wrote_module:
                agent._log("DEBUG", "Ekstraher output verification", "write_file ikke kaldt med et modulnavn — afslutter IKKE auto-complete")
                return None

    # Deterministic phase check (template-defined file existence criteria).
    # Only run after a successful productive tool call — no point
    # auto-completing when the tool itself failed (e.g. update_issue_status
    # with a non-existent issue ID, or edit_file via edit_file2 where the
    # symbol wasn't found but still returned success=True).
    tool_failed = isinstance(tool_result, dict) and not tool_result.get("success")
    # edit_file via edit_file2 pipeline can return success=True even when
    # extraction failed (extract_error). Check for real changes.
    if not tool_failed and tool_name == "edit_file":
        has_changes = isinstance(tool_result, dict) and tool_result.get("lines_changed", 0) > 0
        if not has_changes:
            tool_failed = True
    if not tool_failed:
        PRODUCTIVE_TOOLS = {"write_file", "edit_file", "run_tests", "update_issue_status", "batch_extract_symbols", "extract_symbol", "verify_refactor"}
        if tool_name in PRODUCTIVE_TOOLS:
            try:
                passed, reason = agent_phase_checks.check_phase_done(
                    agent, task_node, called_tools=called_tools,
                    tool_name=tool_name, full_response=full_response,
                )
                if passed:
                    return t(K.PHASE_AUTO_ADVANCED, agent.lang).format(reason=reason)
            except Exception as _e:
                agent._log("DEBUG", f"phase check error: {_e}", "")

    return None



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



def _generate_phase_todos(template: str, phase_name: str, prompt: str = "", agent: Any | None = None) -> list[dict]:
    """Generate a todo checklist for a phase based on template and phase name."""
    phase = _normalize_phase(phase_name).lower()
    todos = []

    if template == "bugfix":
        if phase == "analyse":
            todos.extend([
                {"id": "bf_a1", "text": "L\u00e6s issue med read_issue()", "done": False},
                {"id": "bf_a2", "text": "Find relevant kode med locate()", "done": False},
                {"id": "bf_a3", "text": "Sammenlign koden med buggens p\u00e5stand", "done": False},
                {"id": "bf_a4", "text": "Afg\u00f8r om fejlen findes eller er rettet", "done": False},
            ])
        elif phase == "test" or "test" in phase:
            todos.extend([
                {"id": "bf_t1", "text": "Opret testfil i tests/temp/ med write_file", "done": False},
                {"id": "bf_t2", "text": "K\u00f8r specifik test - den SKAL fejle (r\u00f8d fase)", "done": False},
            ])
        elif phase == "implementering":
            todos.extend([
                {"id": "bf_i1", "text": "Ret kildekoden med edit_file/add_method/add_function", "done": False},
                {"id": "bf_i2", "text": "Undg\u00e5 write_file - filen findes allerede", "done": False},
                {"id": "bf_i3", "text": "Brug add_method til nye metoder i klasse", "done": False},
            ])
        elif phase == "verifikation":
            todos.extend([
                {"id": "bf_v1", "text": "K\u00f8r specifik test - den SKAL best\u00e5 (gr\u00f8n fase)", "done": False},
                {"id": "bf_v2", "text": "K\u00f8r HELE testsuiten for at tjekke regression", "done": False},
            ])
        elif phase == "opdatering":
            todos.append({"id": "bf_o1", "text": "Opdater issue status til 'resolved'", "done": False})

    elif template == "refactor":
        import os as _os
        import re as _re
        # Use session-scoped plan path when available
        if agent and getattr(agent, '_refactor_plan_path', ''):
            _plan_path = agent._refactor_plan_path
        else:
            _plan_path = 'refactor_plan.md'
        plan_content = ''
        plan_fresh = True
        if _os.path.exists(_plan_path):
            try:
                with open(_plan_path, 'r', encoding='utf-8') as _f:
                    plan_content = _f.read()
                if prompt:
                    _prompt_target = _re.search(r'(?:REFAC|ARC|BUG)[-\s]*\d+.*?([a-zA-Z_][\w.]+\.py)', prompt)
                    _plan_target = _re.search(r'([a-zA-Z_][\w.]+\.py)', plan_content[:300])
                    if _prompt_target and _plan_target and _prompt_target.group(1) != _plan_target.group(1):
                        plan_fresh = False
            except (OSError, UnicodeDecodeError):
                pass

        # Extract module names from plan (only if plan is fresh)
        # Matcher både backtick-format (`config.py`) og heading-format (## Modul: config.py)
        _mods = set(_re.findall(r'`([a-zA-Z_][\w.]+\.py)`', plan_content))
        _mods |= set(_re.findall(r'(?:^|\n)#{1,6}\s+(?:Modul:\s*)?([a-zA-Z_][\w]*\.py)', plan_content, _re.MULTILINE | _re.IGNORECASE))
        plan_modules = sorted(_mods) if (plan_content and plan_fresh) else []
        existing_modules = [m for m in plan_modules if _os.path.exists(m)]

        if phase == "analyse":
            todos.extend([
                {"id": "rf_a1", "text": "List alle symboler med list_symbols()", "done": False},
                {"id": "rf_a2", "text": "Læs de vigtigste metoder med read_location()", "done": False},
                {"id": "rf_a3", "text": "Analyser afhængigheder med analyze_dependencies()", "done": False},
                {"id": "rf_a4", "text": "Identificer SOLID-overtrædelser", "done": False},
                {"id": "rf_a5", "text": "Kortlæg ansvarsområder for modulopdeling", "done": False},
                {"id": "rf_a6", "text": "Gem analyse i refactor_analyse.md med write_file()", "done": False},
            ])
        elif phase == "plan":
            todos.extend([
                {"id": "rf_p0", "text": "Læs refactor_analyse.md for at få analyse-resultater", "done": False},
                {"id": "rf_p1", "text": "Beslut modulopdeling", "done": False},
                {"id": "rf_p2", "text": f"Skriv {_plan_path} med write_file()", "done": False},
                {"id": "rf_p3", "text": "Inkluder alle moduler og symboler i planen", "done": False},
            ])
            if existing_modules:
                todos.append({
                    "id": "rf_p_existing",
                    "text": "Findes allerede: {}".format(', '.join(existing_modules)),
                    "done": True
                })

        elif phase == "ekstraher":
            # Parse module→symbols mapping from plan
            _mod_symbols: dict[str, list[str]] = {}
            if plan_content:
                # Pattern 1: "### modul.py\nsymbol1, symbol2, ..." or "## Modul: config.py\n- symbol1\n- symbol2"
                for _m in _re.finditer(
                    r'#{1,4}\s+(?:Modul(?:e)?:\s*)?`?([a-zA-Z_][\w./-]+\.py)`?\s*\n(.*?)(?=\n#{1,4}\s+|$)',
                    plan_content, _re.MULTILINE | _re.DOTALL | _re.IGNORECASE
                ):
                    _mod = _m.group(1).strip('`')
                    _body = _m.group(2).strip()
                    _syms = _re.findall(r'`?([A-Za-z_]\w*)`?', _body)
                    _mod_symbols[_mod] = [s for s in _syms if s not in ('py', 'txt', 'md') and not s.startswith(('.', '/'))]
                # Pattern 2: inline "modul.py → sym1, sym2"
                if not _mod_symbols:
                    for _m in _re.finditer(
                        r'`([a-zA-Z_][\w./-]+\.py)`[^`]*?([A-Z][a-zA-Z_][\w,\s]*)',
                        plan_content
                    ):
                        _mod = _m.group(1)
                        _syms = [s.strip() for s in _m.group(2).split(',') if s.strip()]
                        _mod_symbols[_mod] = _syms

            # Bestem kildefil fra prompt eller plan
            _src_match = _re.search(r"([a-zA-Z_][\w.]+\.py)", prompt or "")
            _ekstraher_src = _src_match.group(1) if _src_match else "api_server.py"

            todos.extend([
                {"id": "rf_e1", "text": "Følg refactor_plan.md nøjagtigt — opfyld ALLE moduler deri", "done": False},
                {"id": "rf_e2", "text": "Brug batch_extract_symbols() til at flytte symboler fra {} til hvert modul".format(_ekstraher_src), "done": False},
                {"id": "rf_e3", "text": "Rækkefølge: batch_extract_symbols → verify_refactor → næste modul", "done": False},
                {"id": "rf_e4", "text": "Verificer syntaks med verify_refactor() efter hver batch", "done": False},
            ])
            # Brug plan_modules eller _mod_symbols keys (fra sektions-headers) som modul-liste
            _all_plan_mods = plan_modules or sorted(_mod_symbols.keys())
            _existing_mods = [m for m in _all_plan_mods if _os.path.exists(m)]
            if _all_plan_mods:
                total = len(_all_plan_mods)
                done_count = len(_existing_mods)
                todos.append({
                    "id": "rf_e_progress",
                    "text": "Fremskridt: {}/{} moduler oprettet ({})".format(done_count, total, ', '.join(_all_plan_mods)),
                    "done": False,
                })
            # Ta l faktiske symboler i eksisterende moduler for status
            if _existing_mods:
                for _mod in _existing_mods:
                    _planned = _mod_symbols.get(_mod, [])
                    _actual = _count_symbols_in_file(_mod)
                    if _planned:
                        _done_count = min(_actual, len(_planned))
                        _status = "{}/{} symbols i filen".format(_done_count, len(_planned))
                    elif _actual > 0:
                        _status = "{} symbols i filen".format(_actual)
                    else:
                        _status = "f\u00e6rdig"
                    todos.append({
                        "id": "rf_e_done_" + _mod.replace('.py', '').replace('.', '_'),
                        "text": "\u2705 {} f\u00e6rdig \u2014 {}".format(_mod, _status),
                        "done": True,
                    })
            to_create = [m for m in _all_plan_mods if m not in _existing_mods]
            for idx, _mod in enumerate(to_create, 1):
                _syms = _mod_symbols.get(_mod, [])
                _count_info = " ({} symbols)".format(len(_syms)) if _syms else ""
                # Afha ngighedsdetektion
                _deps = _detect_module_deps(_mod, _all_plan_mods)
                _dep_info = " \u2014 afh\u00e6nger af: " + ", ".join(_deps) if _deps else ""
                todos.append({
                    "id": "rf_e_create_" + _mod.replace('.py', '').replace('.', '_'),
                    "text": "[{}] Flyt til {}{}{}".format(idx, _mod, _count_info, _dep_info),
                    "done": False,
                })

        elif phase in ("opdater", "opdatering", "opdat\u00e9r"):
            # Determine target file from prompt + plan — fallback to agent_core.py
            _target_file = 'agent_core.py'
            _file_match = _re.search(r"([a-zA-Z_][\w.]+\.py)", prompt)
            if _file_match:
                _target_file = _file_match.group(1)
            if plan_content:
                _plan_match = _re.search(r'([a-zA-Z_][\w.]+\.py)', plan_content[:300])
                if _plan_match and _plan_match.group(1) != _target_file:
                    _target_file = _plan_match.group(1)

            core_path = _os.path.join(_os.environ.get('AGENT_WORKDIR', ''), _target_file) if _os.environ.get('AGENT_WORKDIR') else _target_file
            core_symbols = []
            if _os.path.exists(core_path):
                try:
                    with open(core_path, 'r', encoding='utf-8') as _f:
                        core_content = _f.read()
                    core_nodes = _re.findall(r'^def (\w+)|^class (\w+)', core_content, _re.MULTILINE)
                    core_symbols = sorted(set(n[0] or n[1] for n in core_nodes))
                except (OSError, UnicodeDecodeError):
                    pass

            todos.append({"id": "rf_u1", "text": "List symboler i {} med list_symbols()".format(_target_file), "done": False})

            # Find symbols mentioned in plan that are still in the target file
            # Format: "- `symbol_name` (linje N) -> `target_module.py`"
            if plan_content and core_symbols:
                symbol_map = {}  # symbol -> target_module
                for m in _re.finditer(r'- `(\w+)`[^`]+`([\w.]+\.py)`', plan_content):
                    sym = m.group(1)
                    target = m.group(2)
                    symbol_map[sym] = target
                # Also match explicit symbol_name references
                for m in _re.finditer(r"symbol_name='(\w+)'", plan_content):
                    sym = m.group(1)
                    if sym not in symbol_map:
                        symbol_map[sym] = '?'
                still_in_core = [s for s in symbol_map if s in core_symbols]
                for sym in still_in_core:
                    target_mod = symbol_map.get(sym, '?')
                    todos.append({
                        "id": "rf_u_remove_" + sym,
                        "text": "Fjern `{}` fra {} (i {})".format(sym, _target_file, target_mod),
                        "done": False
                    })

            if existing_modules:
                _target_base = _os.path.splitext(_target_file)[0]
                for mod in existing_modules:
                    mod_name = _os.path.splitext(mod)[0]
                    if mod_name != _target_base:
                        todos.append({
                            "id": "rf_u_import_" + mod_name,
                            "text": "Tilf\u00f8j import fra {} i {}".format(mod, _target_file),
                            "done": False
                        })

            todos.append({"id": "rf_u_verify", "text": "Verificer syntaks med verify_refactor()", "done": False})
            todos.append({"id": "rf_u_tests", "text": "K\u00f8r tests for at bekr\u00e6fte ingen regression", "done": False})
            todos.append({"id": "rf_u_status", "text": "Mark\u00e9r REFAC som resolved med update_issue_status()", "done": False})

        elif phase == "test":
            todos.extend([
                {"id": "rf_t1", "text": "K\u00f8r alle tests for at bekr\u00e6fte ingen regression", "done": False},
                {"id": "rf_t2", "text": "Opdater issue status til 'resolved'", "done": False},
            ])

    elif template == "kodeanalyse":
        if phase == "analyse" or "form\u00e5l" in phase:
            todos.extend([
                {"id": "ka_a1", "text": "List symboler med list_symbols()", "done": False},
                {"id": "ka_a2", "text": "L\u00e6s vigtige funktioner med read_location()", "done": False},
                {"id": "ka_a3", "text": "Analyser afh\u00e6ngigheder", "done": False},
                {"id": "ka_a4", "text": "Skriv analyserapport med write_file()", "done": False},
            ])
        elif "import" in phase:
            todos.extend([
                {"id": "ka_i1", "text": "Gennemg\u00e5 imports med read_location()", "done": False},
                {"id": "ka_i2", "text": "Not\u00e9r ubrugte og cirkul\u00e6re imports", "done": False},
                {"id": "ka_i3", "text": "Skriv import-rapport med write_file()", "done": False},
            ])
        elif "arkitektur" in phase:
            todos.extend([
                {"id": "ka_k1", "text": "Analys\u00e9r klasse- og funktionsstruktur", "done": False},
                {"id": "ka_k2", "text": "Vurd\u00e9r design patterns og SOLID", "done": False},
                {"id": "ka_k3", "text": "Skriv arkitektur-rapport med write_file()", "done": False},
            ])
        elif "kvalitet" in phase or "kodekvalitet" in phase:
            todos.extend([
                {"id": "ka_q1", "text": "Vurd\u00e9r l\u00e6sbarhed, navngivning, type hints", "done": False},
                {"id": "ka_q2", "text": "Tjek test coverage og kompleksitet", "done": False},
                {"id": "ka_q3", "text": "Skriv kodekvalitets-rapport med write_file()", "done": False},
            ])
        elif "sikkerhed" in phase:
            todos.extend([
                {"id": "ka_s1", "text": "Analys\u00e9r inputvalidering og autentifikation", "done": False},
                {"id": "ka_s2", "text": "Tjek for OWASP-top-10 s\u00e5rbarheder", "done": False},
                {"id": "ka_s3", "text": "Skriv sikkerheds-rapport med write_file()", "done": False},
            ])

    elif template == "programmering":
        if phase == "analyse" or "krav" in phase:
            todos.extend([
                {"id": "pr_a1", "text": "Analys\u00e9r krav og behov", "done": False},
                {"id": "pr_a2", "text": "Skriv kravanalyse i docs/kravanalyse.md", "done": False},
            ])
        elif "arkitektur" in phase:
            todos.extend([
                {"id": "pr_d1", "text": "Design arkitektur med komponenter og gr\u00e6nseflader", "done": False},
                {"id": "pr_d2", "text": "Skriv arkitektur i docs/arkitektur.md", "done": False},
            ])
        elif "implementeringsplan" in phase:
            todos.extend([
                {"id": "pr_p1", "text": "Lav implementeringsplan med moduler og r\u00e6kkef\u00f8lge", "done": False},
                {"id": "pr_p2", "text": "Skriv plan i docs/implementeringsplan.md", "done": False},
            ])
        elif "sikkerhed" in phase:
            todos.extend([
                {"id": "pr_s1", "text": "Analys\u00e9r sikkerhedsaspekter", "done": False},
                {"id": "pr_s2", "text": "Skriv sikkerhedsanalyse i docs/sikkerhedsanalyse.md", "done": False},
            ])
        elif "refinement" in phase or "uddyb" in phase:
            todos.extend([
                {"id": "pr_r1", "text": "Udfyld detaljer og pr\u00e6cis\u00e9r specifikationer", "done": False},
            ])
        elif "kodeimplementering" in phase or "implementer" in phase:
            todos.extend([
                {"id": "pr_i1", "text": "Implement\u00e9r koden med write_file()", "done": False},
                {"id": "pr_i2", "text": "K\u00f8r tests med run_tests()", "done": False},
            ])

    elif template == "issue_handler":
        if phase in ("l\u00e6s", "read", "analyse"):
            todos.extend([
                {"id": "ih_a1", "text": "L\u00e6s issue med read_issue()", "done": False},
                {"id": "ih_a2", "text": "Find relevant kode med locate()", "done": False},
            ])
        elif phase in ("afklar", "clarify"):
            todos.extend([
                {"id": "ih_c1", "text": "Forst\u00e5 problemet og afg\u00f8r l\u00f8sning", "done": False},
            ])
        elif phase in ("fix", "implementering"):
            todos.extend([
                {"id": "ih_f1", "text": "Ret koden med edit_file()", "done": False},
                {"id": "ih_f2", "text": "K\u00f8r tests med run_tests()", "done": False},
            ])
        elif phase in ("luk", "close"):
            todos.extend([
                {"id": "ih_l1", "text": "Opdater issue status til 'resolved'", "done": False},
            ])

    elif template == "selvforbedring":
        if phase == "analyse" or "analyser" in phase:
            todos.extend([
                {"id": "sf_a1", "text": "L\u00e6s CORE-issue med read_issue()", "done": False},
                {"id": "sf_a2", "text": "Find relevant kode med locate()", "done": False},
            ])
        elif "diagnostic" in phase:
            todos.extend([
                {"id": "sf_d1", "text": "K\u00f8r tests med run_tests()", "done": False},
                {"id": "sf_d2", "text": "Identific\u00e9r rod\u00e5rsag", "done": False},
            ])
        elif phase in ("ret", "fix"):
            todos.extend([
                {"id": "sf_r1", "text": "Ret koden med edit_file()", "done": False},
                {"id": "sf_r2", "text": "K\u00f8r tests for at bekr\u00e6fte fix", "done": False},
            ])
        elif "verific" in phase or "test" in phase:
            todos.extend([
                {"id": "sf_v1", "text": "K\u00f8r HELE testsuiten", "done": False},
                {"id": "sf_v2", "text": "Opdater CORE-issue status til 'resolved'", "done": False},
            ])
        elif "commit" in phase:
            todos.extend([
                {"id": "sf_c1", "text": "Commit \u00e6ndringer med git_commit()", "done": False},
            ])

    elif template == "testgenerering":
        if phase == "analyse":
            todos.extend([
                {"id": "tg_a1", "text": "Analys\u00e9r koden og find testbare enheder", "done": False},
            ])
        elif "test" in phase:
            todos.extend([
                {"id": "tg_t1", "text": "Opret testfil i tests/temp/ med write_file()", "done": False},
                {"id": "tg_t2", "text": "K\u00f8r test - den SKAL fejle f\u00f8rst (r\u00f8d)", "done": False},
            ])
        elif "implementer" in phase:
            todos.extend([
                {"id": "tg_i1", "text": "Implement\u00e9r koden der g\u00f8r testen gr\u00f8n", "done": False},
                {"id": "tg_i2", "text": "K\u00f8r specifik test - skal best\u00e5", "done": False},
            ])
        elif "verifikation" in phase or "green" in phase:
            todos.extend([
                {"id": "tg_v1", "text": "K\u00f8r HELE testsuiten for regression", "done": False},
            ])

    elif template == "agenten":
        if "branch" in phase:
            todos.extend([
                {"id": "ag_b1", "text": "Opret og skift til ny branch", "done": False},
            ])
        elif "commit" in phase:
            todos.extend([
                {"id": "ag_c1", "text": "Commit \u00e6ndringer", "done": False},
            ])
        elif "push" in phase:
            todos.extend([
                {"id": "ag_p1", "text": "Push til remote", "done": False},
            ])
        elif "pull" in phase or "pr" in phase or "request" in phase:
            todos.extend([
                {"id": "ag_pr1", "text": "Opret Pull Request", "done": False},
            ])

    if not todos:
        todos.append({"id": "todo_generic", "text": f"Gennemf\u00f8r fasen: {phase_name}", "done": False})

    # Add verification todo — auto-marked by reconcile when check_phase_done passes
    todos.append({
        "id": "verify_criteria",
        "text": "Verific\u00e9r at fasens succeskriterier er opfyldt",
        "done": False,
    })

    # Apply language localization to all todos (if not Danish)
    lang = getattr(agent, 'lang', 'da') if agent else 'da'
    if lang and lang != 'da':
        _KEY_MAP: dict[str, str] = {
            # bugfix
            "bf_a1": "bf_a1", "bf_a2": "bf_a2", "bf_a3": "bf_a3", "bf_a4": "bf_a4",
            "bf_t1": "bf_t1", "bf_t2": "bf_t2",
            "bf_i1": "bf_i1", "bf_i2": "bf_i2", "bf_i3": "bf_i3",
            "bf_v1": "bf_v1", "bf_v2": "bf_v2",
            "bf_o1": "bf_o1",
            # refactor
            "rf_u_verify": "rf_u_verify", "rf_u_tests": "rf_u_tests", "rf_u_status": "rf_u_status",
            "rf_t1": "rf_t_tests", "rf_t2": "rf_t_status",
            # kodeanalyse
            "ka_a1": "ka_a1", "ka_a2": "ka_a2", "ka_a3": "ka_a3", "ka_a4": "ka_a4",
            "ka_i1": "ka_i1", "ka_i2": "ka_i2", "ka_i3": "ka_i3",
            "ka_k1": "ka_k1", "ka_k2": "ka_k2", "ka_k3": "ka_k3",
            "ka_q1": "ka_q1", "ka_q2": "ka_q2", "ka_q3": "ka_q3",
            "ka_s1": "ka_s1", "ka_s2": "ka_s2", "ka_s3": "ka_s3",
            # programmering
            "pr_a1": "pr_a1", "pr_a2": "pr_a2",
            "pr_d1": "pr_d1", "pr_d2": "pr_d2",
            "pr_p1": "pr_p1", "pr_p2": "pr_p2",
            "pr_s1": "pr_s1", "pr_s2": "pr_s2",
            "pr_r1": "pr_r1",
            "pr_i1": "pr_i1", "pr_i2": "pr_i2",
            # issue_handler
            "ih_a1": "ih_a1", "ih_a2": "ih_a2",
            "ih_c1": "ih_c1",
            "ih_f1": "ih_f1", "ih_f2": "ih_f2",
            "ih_l1": "ih_l1",
            # selvforbedring
            "sf_a1": "sf_a1", "sf_a2": "sf_a2",
            "sf_d1": "sf_d1", "sf_d2": "sf_d2",
            "sf_r1": "sf_r1", "sf_r2": "sf_r2",
            "sf_v1": "sf_v1", "sf_v2": "sf_v2",
            "sf_c1": "sf_c1",
            # testgenerering
            "tg_a1": "tg_a1",
            "tg_t1": "tg_t1", "tg_t2": "tg_t2",
            "tg_i1": "tg_i1", "tg_i2": "tg_i2",
            "tg_v1": "tg_v1",
            # agenten
            "ag_b1": "ag_b1", "ag_c1": "ag_c1", "ag_p1": "ag_p1", "ag_pr1": "ag_pr1",
        }
        for todo in todos:
            key = _KEY_MAP.get(todo.get("id", ""))
            if key and key in _TEXTS:
                txt = _TEXTS[key].get(lang) or _TEXTS[key].get("en", todo["text"])
                todo["text"] = txt
            elif todo["id"] == "todo_generic":
                todo["text"] = _todo_text("generic_phase", lang, phase_name)
            elif todo["id"] == "verify_criteria":
                todo["text"] = _todo_text("verify_criteria", lang)

    return todos
