"""Agent tasks execution module."""

import ast
import os
import re
import time
import json
import subprocess
from i18n import K
from lang import t
import agent_skills
import agent_git
import agent_files
import agent_issues
import agent_phase_checks
import agent_autoresearch
import config
from typing import Any, Generator
from llm_wrapper import LMStudioWrapper
from config import get_logger
from config_utils import log, EXECUTION_TIMEOUT, _WRITE_TOOLS, FRAMEWORK_PY, _get_max_tool_calls, _get_max_iterations, _set_phase_model, _is_greenfield
from phase_manager import _normalize_phase, PHASE_ALIASES, CLOSE_PHASE_ALIASES, _get_phase_auto_complete_msg, _generate_phase_todos
from prompt_builder import _build_initial_messages, _build_chunk_hint, _build_phase_reason, _cont_hint, _add_user_msg, _msg_content_len, _truncate_messages, _build_truncation_summary, _extract_last_assistant_text
from refactor_helpers import _resolve_refactor_plan_path, _check_import_placement, _refactor_actually_moved_code, _build_refactor_phase_context, _save_full_context_for_refactor, _count_symbols_in_file, _get_symbol_names_in_file, _build_module_progress_msg, _detect_module_deps, _resolve_source_file, _get_modified_core_files, _execute_autoresearch_issue, _check_refactor_progress, _all_planned_modules_exist, AUTO_RESOLVE_PATTERNS, _track_produced_file, _save_llm_prompt_file, _save_maintenance_prompt_dump, _save_llm_log_file
from task_engine import solve_task, solve_task_stream, _finalize_task_stream
from todo_mapper import _TODO_TOOL_MAP, _match_tool_to_todos, _auto_todo_update, _reconcile_llm_todos, _reconcile_todos_with_disk, _auto_populate_llm_todos
from tool_handler import _use_native_tools, set_task_tools, _get_phase_task_tools, _handle_tool_call, _check_required_tools, REQUIRED_ACTION_TOOLS, _ensure_done_tool
from validation import _validate_done_output, _count_fix_attempts, _validate_done_completion, _check_done_pr_requirements, _old_text_was_in_prior_result, _validate_rubrics, _evaluate_rubric_check, _verify_self_modification, _run_full_test_suite, _parse_test_summary, _extract_issue_id, ISSUE_ID_PATTERN
