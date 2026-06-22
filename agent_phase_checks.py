"""Deterministic phase-completion checks for template-driven workflows.

Import facade — symboler er flyttet til moduler:
- file_checks.py: check_file_exists, _resolve, _parse_refactor_plan_modules,
  _has_real_code, _extract_modules_from_plan, check_files_from_plan
- text_tool_checks.py: check_text_contains, check_min_text_length,
  check_tool_called, check_code_contains
- symbol_checks.py: _parse_module_symbols, check_symbols_covered_by_modules,
  check_plan_symbols_per_module, check_plan_symbols_covered
- phase_engine.py: check_all_of, _resolve_phase_key, check_phase_done, check_tests_pass
"""

# file checks
from file_checks import (
    _extract_modules_from_plan,
    _has_real_code,
    _parse_refactor_plan_modules,
    check_file_exists,
    check_files_from_plan,
)

# text / tool checks
from text_tool_checks import (
    check_code_contains,
    check_min_text_length,
    check_text_contains,
    check_tool_called,
)

# symbol checks
from symbol_checks import (
    _parse_module_symbols,
    check_plan_symbols_covered,
    check_plan_symbols_per_module,
    check_symbols_covered_by_modules,
)

# phase engine
from phase_engine import (
    PHASE_ALIASES,
    TEMPLATE_PHASE_CHECKS,
    _resolve_phase_key,
    check_all_of,
    check_phase_done,
    check_tests_pass,
)

__all__ = [
    "PHASE_ALIASES",
    "TEMPLATE_PHASE_CHECKS",
    # file checks
    "_extract_modules_from_plan",
    "_has_real_code",
    "_parse_refactor_plan_modules",
    "check_file_exists",
    "check_files_from_plan",
    # text / tool checks
    "check_code_contains",
    "check_min_text_length",
    "check_text_contains",
    "check_tool_called",
    # symbol checks
    "_parse_module_symbols",
    "check_plan_symbols_covered",
    "check_plan_symbols_per_module",
    "check_symbols_covered_by_modules",
    "check_tests_pass",
    # phase engine
    "_resolve_phase_key",
    "check_all_of",
    "check_phase_done",
]
