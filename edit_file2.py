"""
edit_file2 — AST-aware symbol-level code editing with LLM improvement and retry loop.

Workflow:
  0. Backup the .py file
  1. Find symbol via AST (function/class/method with decorators + leading comments)
  2. Send to LLM for improvement based on requirements
  3. Replace the whole symbol in-place
  4. Test (run pytest)
     4a. If pass → go to 5
     4b. If fail → restore backup, append error to prompt, go to 1
  5. Remove backup
"""

import ast
import os
import shutil
import time

import config
from config import get_logger
from typing import Any

log = get_logger(__name__)

MAX_RETRIES = 3
LLM_TEMPERATURE = 0.3
LLM_MAX_TOKENS = 4096


class EditFile2Error(Exception):
    """edit file2error.
    
    Extends: Exception"""
    pass


# ---------------------------------------------------------------------------
# 1. AST symbol extraction
# ---------------------------------------------------------------------------

def extract_symbol(filepath: str, name: str) -> dict:
    """Extract a full symbol (function/class/method) from a Python file using AST.

    Returns dict with:
      - code: the full source of the symbol (decorators + body)
      - start_line: 1-indexed line number of first decorator or def/class
      - end_line: 1-indexed last line (inclusive)
      - type: 'function', 'async_function', 'class', or 'method'
      - symbol_name: resolved name (e.g. 'MyClass.my_method')
    """
    if not os.path.isfile(filepath):
        raise EditFile2Error(f"File not found: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    source = "".join(lines)

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise EditFile2Error(f"Syntax error in {os.path.basename(filepath)}: {e}")

    parts = name.split(".", 1)
    func_name = parts[-1]
    class_name = parts[0] if len(parts) == 2 else None

    node = _find_node(tree, func_name, class_name)
    if node is None:
        raise EditFile2Error(
            f"Symbol '{name}' not found in {os.path.basename(filepath)}"
        )

    # Walk backwards from node.lineno to capture decorators and leading comments
    start_line = _find_symbol_start(lines, node)
    end_line = getattr(node, "end_lineno", node.lineno) or node.lineno

    code = "".join(lines[start_line - 1 : end_line])

    node_type = type(node).__name__
    if node_type == "AsyncFunctionDef":
        node_type = "async_function"
    elif node_type == "FunctionDef":
        node_type = "function"
    elif node_type == "ClassDef":
        node_type = "class"

    return {
        "code": code,
        "start_line": start_line,
        "end_line": end_line,
        "type": node_type,
        "symbol_name": name,
    }


def _find_node(tree: ast.AST, func_name: str, class_name: str | None = None) -> ast.AST | None:
    """Walk AST to find a FunctionDef / AsyncFunctionDef / ClassDef node."""
    for node in ast.walk(tree):
        if class_name:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if child.name == func_name:
                            return child
                return None
        else:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == func_name:
                    return node
            elif isinstance(node, ast.ClassDef):
                if node.name == func_name:
                    return node
    return None


def _find_symbol_start(lines: list[str], node: ast.AST) -> int:
    """Find the first line of a symbol including decorators and leading comments."""
    start = node.lineno
    # Walk backwards through decorators
    for deco in node.decorator_list:
        if deco.lineno < start:
            start = deco.lineno
    # Walk backwards through leading comments / blank lines
    idx = start - 2  # line before first decorator or def
    while idx >= 0:
        stripped = lines[idx].strip()
        if stripped.startswith("#") or stripped == "":
            idx -= 1
        else:
            break
    return idx + 2  # first comment or decorator line


# ---------------------------------------------------------------------------
# 2. LLM improvement
# ---------------------------------------------------------------------------

def improve_with_llm(llm: Any, symbol_code: str, requirements: str, error_context: str | None = None, symbol_name: str = "") -> tuple[str, str | None]:
    """Send symbol code to LLM for improvement.

    Args:
        llm: LMStudioWrapper instance
        symbol_code: the current code of the symbol
        requirements: what the user wants changed
        error_context: error output from a failed test (retry loop)
        symbol_name: name of the symbol for context

    Returns: (code, syntax_error) tuple
        - code: improved code as a string
        - syntax_error: None if valid, error message if syntax invalid
    """
    system_msg = (
        "You are an expert Python developer. Improve the given code according to the requirements. "
        "Return ONLY the improved code — no explanations, no markdown fences, no commentary. "
        "The code must be syntactically valid Python and keep the same function/method signature. "
        "Preserve all imports used by the code. Do not add or remove parameters."
    )

    user_msg = f"## Symbol: {symbol_name}\n\n```python\n{symbol_code}\n```\n\n"
    user_msg += f"## Requirements\n\n{requirements}\n"

    if error_context:
        user_msg += (
            f"\n## Previous attempt failed with this error\n\n"
            f"```\n{error_context[:2000]}\n```\n\n"
            "Fix the error and try again. Return only the corrected code."
        )

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    response = llm.generate(
        messages=messages,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS,
        use_cache=False,
    )

    if not response or response.startswith("ERROR:"):
        raise EditFile2Error(f"LLM failed: {response}")

    # Strip markdown fences if present
    code = response.strip()
    if code.startswith("```python"):
        code = code[len("```python"):].strip()
    elif code.startswith("```"):
        code = code[3:].strip()
    if code.endswith("```"):
        code = code[:-3].strip()

    # Validate syntax
    syntax_error = None
    try:
        ast.parse(code)
    except SyntaxError as e:
        syntax_error = f"Syntax error in LLM output: {e}"
        log.warning("LLM returned code with syntax errors: %s", e)

    return code, syntax_error


# ---------------------------------------------------------------------------
# 3. Symbol replacement
# ---------------------------------------------------------------------------

def replace_symbol(filepath: str, symbol_info: dict, new_code: str) -> None:
    """Replace a symbol in the file with new code.

    Args:
        filepath: path to the .py file
        symbol_info: dict from extract_symbol()
        new_code: the new code to insert
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    start = symbol_info["start_line"] - 1  # 0-indexed
    end = symbol_info["end_line"]  # exclusive in slice

    # Ensure new_code ends with exactly one newline
    new_code = new_code.rstrip("\n") + "\n"

    new_lines = [new_code]
    result = lines[:start] + new_lines + lines[end:]

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(result)


# ---------------------------------------------------------------------------
# 4. Testing
# ---------------------------------------------------------------------------

def run_test(test_path: str) -> tuple[bool, str]:
    """Run pytest and return (success, output)."""
    try:
        from agent_issues import run_pytest
        result = run_pytest(test_path)
        success = result.get("success", False)
        output = result.get("stdout", "") + result.get("stderr", "")
        return success, output[:2000]
    except Exception as e:
        return False, str(e)[:2000]



# ---------------------------------------------------------------------------
# 5. Main orchestrator
# ---------------------------------------------------------------------------

def edit_file2(filepath: str, name: str, requirements: str, llm: Any, test_path: str | None = None, max_retries: int | None = None) -> dict:
    """AST-aware symbol editing with LLM improvement and retry loop.

    Args:
        filepath: path to the .py file
        name: symbol name (e.g. 'edit_file' or 'MyClass.my_method')
        requirements: what to change (from the prompt)
        llm: LMStudioWrapper instance
        test_path: optional pytest path (e.g. 'tests/test_git_ops.py')
        max_retries: max retry attempts (default MAX_RETRIES)

    Returns: dict with status, attempts, final code, etc.
    """
    if max_retries is None:
        max_retries = MAX_RETRIES

    filepath = os.path.abspath(filepath)
    backup_path = filepath + ".edit2.bak"
    error_context = None
    attempts = []
    last_new_code = None

    # 0. Backup
    try:
        shutil.copy2(filepath, backup_path)
        log.info("Backup created: %s", backup_path)
    except Exception as e:
        raise EditFile2Error(f"Backup failed: {e}")

    try:
        for attempt in range(1, max_retries + 1):
            log.info("edit_file2 attempt %d/%d for %s.%s", attempt, max_retries, filepath, name)

            # 1. Extract symbol
            try:
                symbol = extract_symbol(filepath, name)
            except EditFile2Error as e:
                attempts.append({"attempt": attempt, "status": "extract_error", "error": str(e)})
                break

            # 2. LLM improvement
            try:
                new_code, syntax_error = improve_with_llm(
                    llm, symbol["code"], requirements,
                    error_context=error_context, symbol_name=name,
                )
            except EditFile2Error as e:
                attempts.append({"attempt": attempt, "status": "llm_error", "error": str(e)})
                break

            # If syntax error, add to error context and retry
            if syntax_error:
                error_context = syntax_error
                attempts.append({"attempt": attempt, "status": "syntax_error", "error": syntax_error})
                shutil.copy2(backup_path, filepath)
                continue

            last_new_code = new_code

            # 3. Replace symbol
            try:
                replace_symbol(filepath, symbol, new_code)
                log.info("Symbol replaced: %s lines %d-%d", name, symbol["start_line"], symbol["end_line"])
            except Exception as e:
                attempts.append({"attempt": attempt, "status": "replace_error", "error": str(e)})
                # Restore and retry
                shutil.copy2(backup_path, filepath)
                continue

            # 4. Test (if test_path provided)
            if test_path:
                success, output = run_test(test_path)
                if success:
                    log.info("Tests passed on attempt %d", attempt)
                    attempts.append({"attempt": attempt, "status": "test_passed"})
                    break
                else:
                    log.warning("Tests failed on attempt %d: %s", attempt, output[:200])
                    error_context = output
                    attempts.append({"attempt": attempt, "status": "test_failed", "error": output[:500]})
                    # Restore backup for next attempt
                    shutil.copy2(backup_path, filepath)
                    log.info("Backup restored for retry")
            else:
                # No test — trust the LLM
                log.info("No test_path provided, accepting result on attempt %d", attempt)
                attempts.append({"attempt": attempt, "status": "accepted_no_test"})
                break

        else:
            # All retries exhausted
            log.error("edit_file2: all %d attempts exhausted for %s.%s", max_retries, filepath, name)
            # Restore backup on final failure
            shutil.copy2(backup_path, filepath)
            return {
                "success": False,
                "error": f"All {max_retries} attempts failed",
                "attempts": attempts,
                "file": filepath,
                "symbol": name,
            }

        # 5. Remove backup on success
        try:
            if os.path.exists(backup_path):
                os.remove(backup_path)
                log.info("Backup removed: %s", backup_path)
        except Exception:
            pass  # non-critical

        return {
            "success": True,
            "attempts": len(attempts),
            "attempt_details": attempts,
            "file": filepath,
            "symbol": name,
            "new_code_preview": last_new_code[:200] + "..." if last_new_code and len(last_new_code) > 200 else last_new_code,
        }

    except Exception as e:
        # Ensure backup is restored on any unexpected error
        try:
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, filepath)
        except Exception:
            pass
        raise EditFile2Error(f"edit_file2 failed: {e}")
