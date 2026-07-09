from config import get_logger
import os
import time as _time
import ast
import json
import textwrap
from lang import t

log = get_logger(__name__)



_BUILTINS: frozenset[str] = frozenset({
    'abs', 'all', 'any', 'bool', 'bytes', 'callable', 'chr', 'classmethod',
    'compile', 'complex', 'delattr', 'dict', 'dir', 'divmod', 'enumerate',
    'eval', 'exec', 'filter', 'float', 'format', 'frozenset', 'getattr',
    'globals', 'hasattr', 'hash', 'hex', 'id', 'input', 'int', 'isinstance',
    'issubclass', 'iter', 'len', 'list', 'locals', 'map', 'max', 'min',
    'next', 'object', 'oct', 'open', 'ord', 'pow', 'print', 'property',
    'range', 'repr', 'reversed', 'round', 'set', 'setattr', 'slice',
    'sorted', 'staticmethod', 'str', 'sum', 'super', 'tuple', 'type',
    'vars', 'zip',
    'True', 'False', 'None', 'self', 'cls',
    'Exception', 'ValueError', 'TypeError', 'KeyError', 'IndexError',
    'AttributeError', 'ImportError', 'ModuleNotFoundError', 'StopIteration',
    'RuntimeError', 'OSError', 'IOError', 'FileNotFoundError', 'NotImplementedError',
})


_BUILTINS_TYPING: frozenset[str] = frozenset({
    'Any', 'Optional', 'List', 'Dict', 'Tuple', 'Set', 'Callable',
    'TypeVar', 'Generic', 'Protocol', 'Union', 'Final', 'ClassVar',
    'Sequence', 'Iterable', 'Iterator', 'Generator',
})


# Static mapping of known framework symbols → their import paths.
# batch_extract_symbols auto-adds these to target modules so extracted
# functions actually compile without manual import fixing.
_KNOWN_SYMBOL_IMPORTS: dict[str, str] = {
    # Flask — bare symbols (typically from flask import X)
    "Flask": "flask",
    "request": "flask",
    "jsonify": "flask",
    "Response": "flask",
    "send_from_directory": "flask",
    "stream_with_context": "flask",
    "url_for": "flask",
    "redirect": "flask",
    "abort": "flask",
    "make_response": "flask",
    "send_file": "flask",
    "session": "flask",
    "g": "flask",
    "current_app": "flask",
    "copy_current_request_context": "flask",
    "has_request_context": "flask",
    # Flask extension
    "CORS": "flask_cors",
    # Config
    "app": "config",
    "BASE_DIR": "config",
    "STATIC_DIR": "config",
    "VERSION_FILES": "config",
    "BUILD_INFO": "config",
    "get_logger": "config",
    "log": "config",
    "_is_development_mode": "config",
    "_file_mtime": "config",
    "active_streams": "config",
    "active_streams_lock": "config",
    "current_session_lock": "config",
    # Session manager
    "SessionManager": "session_manager",
    "session_manager": "session_manager",
    "agent": "session_manager",
    "current_session_id": "session_manager",
    "_guard_json_body": "session_manager",
    "execution_status": "session_manager",
    "execution_status_lock": "session_manager",
    "export_folder": "session_manager",
    "export_folder_lock": "session_manager",
    # Agent core
    "Agent": "agent_core",
    # Framework modules
    "TEMPLATE_PHASE_CHECKS": "agent_phase_checks",
    "check_phase_done": "agent_phase_checks",
    "clear_extracted_registry": "refactoring_engine",
    "LMStudioWrapper": "llm_wrapper",
    "K": "i18n",
    "t": "lang",
    "get_ui_translations": "lang",
}


# Reverse map: module → list of (symbol, alias) pairs for from X import Y
_KNOWN_MODULE_SYMBOLS: dict[str, list[tuple[str, str | None]]] = {}



# Globalt register over allerede ekstraherede symboler pr. source-fil.
# Nøgle: absolut sti til source-filen.
# Værdi: sæt af symbolnavne der allerede er flyttet til en target.
# Nulstilles eksplicit vha. clear_extracted_registry() ved sessionsstart.
_extracted_registry: dict[str, set[str]] = {}



def _atomic_replace(src: str, dst: str, max_retries: int = 8) -> None:
    """Replace dst with src atomically, retrying on Windows file locks."""
    for attempt in range(max_retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt < max_retries - 1:
                _time.sleep(0.15 * (attempt + 1))
            else:
                raise



def _parse_symbols_list(symbols: str | list[str]) -> list[str]:
    """Parse the ``symbols`` parameter into a clean list of symbol names.

    Handles all formats commonly sent by LLMs:
    - ``["sym1", "sym2"]`` — JSON array string
    - ``['sym1', 'sym2']`` — Python list string
    - ``sym1, sym2, sym3`` — comma-separated string
    - ``["sym1", "sym2"]`` as actual list (from JSON API)
    """
    if isinstance(symbols, list):
        return [str(s).strip() for s in symbols if str(s).strip()]

    s = str(symbols).strip()

    # Try JSON array: ["sym1", "sym2"]
    if s.startswith("[") and s.endswith("]"):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(item).strip(" \t\"'") for item in parsed if item]
        except (json.JSONDecodeError, TypeError):
            pass

    # Try Python list: ['sym1', 'sym2']
    if s.startswith("[") and s.endswith("]"):
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, list):
                return [str(item).strip(" \t\"'") for item in parsed if item]
        except (ValueError, SyntaxError, TypeError):
            pass

    # Comma-separated
    if "," in s:
        return [p.strip(" \t\"'[]") for p in s.split(",") if p.strip(" \t\"'[]")]

    # Space-separated (no commas found)
    parts = [p.strip(" \t\"'[]") for p in s.split() if p.strip(" \t\"'[]")]
    if parts:
        return parts

    return []



def _auto_add_known_imports(target: str) -> list[str]:
    """Scan a Python file for used-but-not-imported names and add known imports.

    Checks each Name reference in the target file against
    ``_KNOWN_SYMBOL_IMPORTS``. If an unimported name matches, adds the
    corresponding ``from <module> import <name>`` at the top of the file.

    Args:
        target: Path to the target .py file.

    Returns:
        List of import strings that were added (empty if none needed).
    """
    if not os.path.exists(target):
        return []
    try:
        with open(target, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    # Collect names already defined in the file
    local_defs: set[str] = set()
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            local_defs.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    local_defs.add(t.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                local_defs.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imports.add(alias.asname or alias.name)
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.update(alias.asname or alias.name for alias in node.names)

    # Collect parameter names from all functions
    param_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in node.args.args:
                param_names.add(arg.arg)
            if node.args.vararg:
                param_names.add(node.args.vararg.arg)
            if node.args.kwarg:
                param_names.add(node.args.kwarg.arg)

    # Collect all Name references (Load context only)
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in local_defs and node.id not in imports \
               and node.id not in param_names and node.id not in _BUILTINS \
               and node.id not in _BUILTINS_TYPING:
                used.add(node.id)

    # For remaining names, check if they're in KNOWN_SYMBOL_IMPORTS
    needed_imports: dict[str, set[str]] = {}
    for name in used:
        mod = _KNOWN_SYMBOL_IMPORTS.get(name)
        if mod:
            needed_imports.setdefault(mod, set()).add(name)

    if not needed_imports:
        return []

    # Read lines and insert imports after existing imports
    lines = content.split("\n")
    insert_pos = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            insert_pos = i + 1
        elif stripped.startswith(('"""', "'''", "#")) and insert_pos == 0:
            # Docstring or comment at start — keep going
            continue
        elif stripped == "" and insert_pos == 0:
            continue
        elif insert_pos > 0:
            break
        else:
            break

    new_imports: list[str] = []
    for mod in sorted(needed_imports):
        symbols = sorted(needed_imports[mod])
        # Check if these exact imports already exist
        existing_imports_str = "\n".join(lines)
        already_there = True
        for sym in symbols:
            pat = f"from {mod} import "
            if pat in existing_imports_str:
                # Already importing from this module
                continue
            already_there = False
        if already_there:
            # All symbols already imported — skip
            continue
        new_imports.append(f"from {mod} import {', '.join(symbols)}")

    if not new_imports:
        return []

    # Insert after the last import line
    for imp in new_imports:
        lines.insert(insert_pos, imp)
        insert_pos += 1

    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log.info("_auto_add_known_imports: added %d imports to %s: %s",
                 len(new_imports), os.path.basename(target), "; ".join(new_imports))
    except OSError as e:
        log.warning("_auto_add_known_imports: write failed: %s", e)
        return []

    return new_imports



def _list_top_level_symbol_names(content: str) -> set[str]:
    """Return set of all top-level function/class/variable names in content."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names



def _find_unresolved_local_deps(source_content: str, target_content: str) -> list[str]:
    """Find names used in target_content that are local symbols in source_content.

    These are names that a target file references but that are only defined
    as top-level symbols in the source file (not builtins, not imported,
    not defined in the target itself). This detects the case where extracting
    a function like ``create_user() -> User`` leaves ``User`` undefined in
    the target because ``User`` is a class in the source — not an import.
    """
    try:
        source_tree = ast.parse(source_content)
    except SyntaxError:
        return []
    try:
        target_tree = ast.parse(target_content)
    except SyntaxError:
        return []

    source_symbols = _list_top_level_symbol_names(source_content)

    # Collect names defined/imported in target
    target_defined = _list_top_level_symbol_names(target_content)
    target_imported: set[str] = set()
    for node in ast.walk(target_tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            from ast_analyzer import AstAnalyzer
            target_imported |= AstAnalyzer.names_from_import_node(node)

    all_known = target_defined | target_imported | _BUILTINS | _BUILTINS_TYPING

    # Collect ALL Name nodes in target (except imports and definitions)
    used_in_target: set[str] = set()
    for node in ast.walk(target_tree):
        if isinstance(node, ast.Name):
            name = node.id
            # Skip names that are being defined (target of assignment, function def, etc.)
            # We only care about references, not definitions
            if name not in all_known and name not in source_symbols:
                continue
            # Check if this is a reference (not a definition context)
            if name not in target_defined:
                used_in_target.add(name)

    unresolved = sorted(used_in_target & source_symbols)
    return [u for u in unresolved if u not in all_known]



def _detect_import_cycle_risk(
    source_content: str,
    source_path: str,
    target_path: str,
    symbol_code: str,
) -> list[str]:
    """Detect if extracted symbol code references names from source that would
    create a circular import if imported into target.

    A circular import risk exists when:
    1. The extracted code references names defined at module level in source
       (not imports, not builtins, not defined in the symbol itself)
    2. The source file already imports from the target module

    Returns list of risky name references (names that need importing from
    source into target, but source already imports from target → cycle).
    """
    if not os.path.exists(target_path):
        return []

    try:
        source_tree = ast.parse(source_content)
    except SyntaxError:
        return []
    try:
        symbol_tree = ast.parse(textwrap.dedent(symbol_code))
    except SyntaxError:
        return []

    # Names defined locally in the symbol itself
    local_names: set[str] = set()
    for node in ast.walk(symbol_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            local_names.add(node.name)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            local_names.add(node.id)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    local_names.add(t.id)

    source_symbols = _list_top_level_symbol_names(source_content)

    # Names referenced in the symbol (Load) that are defined in source
    referenced_from_source: set[str] = set()
    for node in ast.walk(symbol_tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in source_symbols and node.id not in local_names:
                referenced_from_source.add(node.id)

    if not referenced_from_source:
        return []

    # Check if source already imports from target → would create a cycle
    target_module_name = os.path.splitext(os.path.basename(target_path))[0]
    for node in ast.walk(source_tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and (node.module == target_module_name or node.module.split('.')[0] == target_module_name):
                return sorted(referenced_from_source)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == target_module_name or alias.name.split('.')[0] == target_module_name:
                    return sorted(referenced_from_source)

    return []



def _split_imports_from_code(content: str) -> tuple[str, str]:
    """Split file content into (imports_block, code_block).

    All consecutive top-level import statements at the start of the file
    are collected into the imports_block. Everything else is code_block.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return "", content

    lines = content.split('\n')
    last_import_end = 0
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            end = getattr(node, 'end_lineno', node.lineno) or node.lineno
            if last_import_end == 0 or end < last_import_end + 3:
                if end > last_import_end:
                    last_import_end = end
            else:
                break

    if last_import_end == 0:
        return "", content

    import_lines = lines[:last_import_end]
    code_lines = lines[last_import_end:]
    return '\n'.join(import_lines), '\n'.join(code_lines).strip('\n')



def _registry_key(source: str) -> str:
    """Generer en nøgle for registret: absolut sti til source-filen.

    Tidligere brugte denne funktion et hash til at detektere source-reverts,
    men det ødelagde registret efter hver extraction (fordi filen ændrer
    sig når et symbol fjernes). Nu bruges kun absolut sti som nøgle.
    """
    return os.path.abspath(source)



def _is_nested_function(tree: ast.AST, node: ast.AST) -> bool:
    """Check if a FunctionDef/AsyncFunctionDef is nested inside another function.

    Returns True if the node is a function defined INSIDE another function
    (not a class method, not module-level). Uses AST parent-walking by
    scanning all FunctionDef/AsyncFunctionDef bodies for the node reference.
    """
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for candidate in ast.walk(tree):
        if candidate is tree or candidate is node:
            continue
        if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.iter_child_nodes(candidate):
                if child is node:
                    return True
    return False



def clear_extracted_registry(source: str | None = None) -> None:
    """Nulstil registret for én eller alle source-filer.

    Args:
        source: Hvis angivet, nulstilles kun for denne fil.
                Hvis None, nulstilles hele registret.
    """
    if source:
        _extracted_registry.pop(os.path.abspath(source), None)
    else:
        _extracted_registry.clear()



def _mark_extracted(source: str, symbol: str) -> None:
    """Registrér at et symbol er blevet ekstraheret fra source."""
    key = _registry_key(source)
    _extracted_registry.setdefault(key, set()).add(symbol)



def _is_already_extracted(source: str, symbol: str) -> bool:
    """Tjek om et symbol allerede er ekstraheret fra denne source."""
    key = _registry_key(source)
    return symbol in _extracted_registry.get(key, set())



def _extract_module_from_import(import_stmt: str) -> str | None:
    """Extract module name from an import statement string.

    'from flask import request' → 'flask'
    'import os' → 'os'
    Returns None if it can't parse.
    """
    try:
        tree = ast.parse(import_stmt)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                return node.module
            if isinstance(node, ast.Import) and node.names:
                return node.names[0].name
    except SyntaxError:
        pass
    return None



def _has_back_import(imp_module: str, target_module: str) -> bool:
    """Tjek om imp_module allerede importerer fra target_module.

    Brugt til at forhindre circular imports: hvis target importerer
    fra imp_module, men imp_module allerede har 'from target import X'.
    """
    if not os.path.exists(imp_module):
        # Prøv med .py tilføjelse
        imp_module_py = imp_module + '.py' if not imp_module.endswith('.py') else imp_module
        if not os.path.exists(imp_module_py):
            return False
        imp_module = imp_module_py
    target_base = os.path.splitext(os.path.basename(target_module))[0]
    try:
        with open(imp_module, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod_name = os.path.splitext(os.path.basename(node.module))[0]
                if mod_name == target_base:
                    return True
        return False
    except (OSError, SyntaxError):
        return False
