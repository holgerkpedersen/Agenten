from config import get_logger
import config
import re
from typing import Any, Generator
from agent_refactor_helpers import _all_planned_modules_exist

log = get_logger(__name__)


EXECUTION_TIMEOUT = config.EXECUTION_TIMEOUT


_WRITE_TOOLS = frozenset({"write_file", "edit_file", "write_file_section", "convert_pdf_html5"})



PHASE_ALIASES = {
    "analyse": "analyse", "analysis": "analyse",
    "test": "test",
    "implementering": "implementering", "implementation": "implementering",
    "verifikation": "verifikation", "verification": "verifikation", "green": "verifikation",
    "opdatering": "opdatering", "update": "opdatering",
    "ekstraher": "ekstraher", "extract": "ekstraher",
    "plan": "plan",
    "opdatér": "opdatér",
    "læs": "analyse", "read": "analyse",
    "afklar": "analyse", "clarify": "analyse",
    "afklar & opdater": "analyse", "clarify & update": "analyse",
    "verificer": "analyse", "verify": "analyse",
    "fix": "fix",
    "luk": "luk", "close": "luk",
    "luk issue": "luk", "close issue": "luk",
}



REQUIRED_ACTION_TOOLS = {"edit_file", "write_file", "delete_file", "extract_symbol", "remove_symbol", "add_import", "add_method", "add_function", "update_issue_status"}



CLOSE_PHASE_ALIASES = {"opdatering", "opdatér", "update", "completion", "luk", "close", "cerrar", "actualizar", "finalizar", "关闭", "完成", "更新"}



ISSUE_ID_PATTERN = re.compile(r'(BUG|SEC|ARC|MNT|PRF|TST|REFAC|STAB)-\d+', re.IGNORECASE)



AUTO_RESOLVE_PATTERNS = [
    r'allerede (?:løst|rettet|fikset|fixet)',
    r'(?:fejlen|buggen|problemet) (?:findes ikke|eksisterer ikke|er væk)',
    r'koden er allerede (?:korrekt|rettet|fikset)',
    r'allerede (?:implementeret|udført)',
    r'already (?:fixed|resolved|solved|correct)',
    r'(?:bug|issue|problem) no longer (?:exists|reproducible|applicable)',
    r'(?:no change|nothing to fix)',
    r'intet at (?:rette|fikse|gøre)',
    r'ya (?:solucionado|corregido|arreglado)',
    r'(?:el error|el bug|el problema) (?:no existe|ya no es reproducible)',
    r'el código ya es (?:correcto|corregido)',
    r'ya (?:implementado|completado)',
    r'nada que (?:arreglar|corregir|hacer)',
    r'(?:bug|问题) (?:不再存在|已修复|不再可重现)',
    r'(?:无需修复|无变化|没有问题)',
    r'已经(?:修复|解决|修正|实现)',
]
AUTO_RESOLVE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in AUTO_RESOLVE_PATTERNS]



FRAMEWORK_PY = {"api_server.py", "agent_core.py", "agent_tasks.py", "agent_skills.py", "agent_files.py", "agent_issues.py", "agent_tree.py", "agent_git.py", "agent_phase_checks.py", "agent_wta.py", "core_analytics.py", "agent_logs.py", "tools.py", "i18n.py", "lang.py", "config.py", "task_tree.py", "llm_wrapper.py", "model_manager.py", "session_manager.py", "flow_builder.py", "skill_evolution.py", "skill_loader.py", "skill_tracker.py", "refactoring_engine.py", "github_wrapper.py"}



# Tool-to-todo mapping: (tool_name, arg_check_func_or_none) -> todo_id
_TODO_TOOL_MAP: list[tuple[str, Any | None, str]] = [
    ("read_issue", None, "bf_a1"),
    ("locate", None, "bf_a2"),
    ("list_symbols", None, "rf_a1"),
    ("read_location", None, "bf_a3"),
    ("analyze_dependencies", None, "rf_a3"),
    ("write_file", lambda a: "refactor_plan" in str(a.get("path", "")), "rf_p2"),
    ("write_file", lambda a: "refactor_analyse" in str(a.get("path", "")), "rf_a6"),
    ("write_file", lambda a: "tests/temp" in str(a.get("path", "")), "bf_t1"),
    ("write_file", lambda a: "docs/" in str(a.get("path", "")) and a.get("path","").endswith(".md"), "ka_a4"),
    ("write_file", lambda a: "docs/" in str(a.get("path", "")), "pr_a2"),
    ("extract_symbol", None, "rf_e1"),
    # rf_e1 ("Følg refactor_plan.md nøjagtigt — opfyld ALLE moduler deri")
    # ma kun markeres done naar ALLE planlagte moduler eksisterer.
    ("batch_extract_symbols", lambda a: _all_planned_modules_exist(a), "rf_e1"),
    ("batch_extract_symbols", None, "rf_e2"),
    ("batch_extract_symbols", None, "rf_e3"),
    ("extract_symbol", None, "rf_e2"),
    ("extract_symbol", None, "rf_e3"),
    ("add_method", None, "bf_i3"),
    ("add_function", None, None),
    ("verify_refactor", None, "rf_e4"),
    ("run_tests", None, "bf_t2"),
    ("run_tests", None, "rf_u_tests"),
    ("run_tests", None, "rf_t1"),
    ("run_tests", None, "sf_d1"),
    ("run_tests", None, "sf_v1"),
    ("run_tests", None, "tg_v1"),
    ("run_tests", None, "pr_i2"),
    ("run_tests", None, "ih_f2"),
    ("update_issue_status", None, "bf_o1"),
    ("update_issue_status", None, "rf_u_status"),
    ("update_issue_status", None, "rf_t2"),
    ("update_issue_status", None, "sf_v2"),
    ("update_issue_status", None, "ih_l1"),
    ("verify_refactor", None, "rf_u_verify"),
    ("list_symbols", None, "ka_a1"),
    ("edit_file", None, "ih_f1"),
    ("edit_file", None, "sf_r1"),
    ("read_issue", None, "ih_a1"),
    ("read_issue", None, "sf_a1"),
    ("git_create_branch", None, "ag_b1"),
    ("git_commit", None, "ag_c1"),
    ("git_commit", None, "sf_c1"),
    ("git_push", None, "ag_p1"),
    ("github_create_pr", None, "ag_pr1"),
]
