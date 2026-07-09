import ast
from refactor_utils import log, _BUILTINS, _BUILTINS_TYPING, _KNOWN_SYMBOL_IMPORTS, _KNOWN_MODULE_SYMBOLS, _extracted_registry, _atomic_replace, _parse_symbols_list, _auto_add_known_imports, _list_top_level_symbol_names, _find_unresolved_local_deps, _detect_import_cycle_risk, _split_imports_from_code, _registry_key, _is_nested_function, clear_extracted_registry, _mark_extracted, _is_already_extracted, _extract_module_from_import, _has_back_import
from refactor_error import RefactoringError, FileSnapshot



class CodeModifier:
    """Deterministic code modification operations."""

    @staticmethod
    def remove_lines(path: str, start: int, end: int) -> str:
        """Remove lines from a file. start/end are 0-indexed, end is exclusive.

        Returns the new content. Validates syntax after removal.
        """
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().replace('\r\n', '\n').replace('\r', '\n')
        lines = content.split('\n')

        new_lines = lines[:start] + lines[end:]

        # Compress runs of 3+ consecutive blank lines to at most 2
        compressed = []
        blank_run = 0
        for line in new_lines:
            if line.strip() == '':
                blank_run += 1
                if blank_run <= 2:
                    compressed.append(line)
            else:
                blank_run = 0
                compressed.append(line)
        new_content = '\n'.join(compressed).strip() + '\n'

        try:
            ast.parse(new_content)
        except SyntaxError as e:
            raise RefactoringError(
                f"Syntax error after removal: {e}",
                category=RefactoringError.SYNTAX,
                filepath=path,
                details={"line": e.lineno, "msg": e.msg}
            )

        tmppath = path + '.tmp'
        with open(tmppath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        _atomic_replace(tmppath, path)

        return new_content

    @staticmethod
    def insert_import(path: str, import_stmt: str) -> bool:
        """Insert an import statement into a Python file after existing imports.

        Returns True if import was added, False if it already exists.
        Merges with existing same-module imports (e.g. 'from os import path'
        becomes 'from os import path, walk' instead of adding a separate line).
        """
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().replace('\r\n', '\n').replace('\r', '\n')

        if import_stmt in content:
            return False

        tree = ast.parse(content)
        lines = content.split('\n')

        # Try to merge with existing same-module ImportFrom
        import ast as _ast
        try:
            parsed_import = _ast.parse(import_stmt)
            merge_candidate = (parsed_import and parsed_import.body and
                               isinstance(parsed_import.body[0], _ast.ImportFrom))
        except SyntaxError:
            merge_candidate = False
        if merge_candidate:
            new_node = parsed_import.body[0]
            new_module = new_node.module
            new_name = new_node.names[0].name if new_node.names else ""

            for node in _ast.iter_child_nodes(tree):
                if isinstance(node, _ast.ImportFrom) and node.module == new_module:
                    # Same module — check if symbol already exists in this import
                    existing_names = {alias.name for alias in node.names}
                    if new_name in existing_names:
                        return False  # Already imported
                    # Merge: add symbol to existing import line
                    old_line_num = node.lineno - 1  # 0-indexed
                    old_line = lines[old_line_num]
                    # Find the last name in the existing import
                    last_name = node.names[-1].name
                    # Replace the closing paren or add to existing paren
                    if old_line.rstrip().endswith(')'):
                        # Multi-line import or parenthesized — insert before closing )
                        new_line = old_line.rstrip()
                        insert_pos = new_line.rfind(')')
                        new_line = new_line[:insert_pos] + ', ' + new_name + new_line[insert_pos:]
                        lines[old_line_num] = new_line
                    else:
                        # Single-line import: from X import a → from X import a, b
                        lines[old_line_num] = old_line.rstrip() + ', ' + new_name
                    new_content = '\n'.join(lines)
                    try:
                        _ast.parse(new_content)
                    except SyntaxError:
                        # Fall through to normal insert below
                        lines = content.split('\n')
                        break
                    # Write merged result
                    tmppath = path + '.tmp'
                    with open(tmppath, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    _atomic_replace(tmppath, path)
                    return True

        # Normal insert: insert right after the first consecutive import block
        # (standard Python convention — imports at the top of the file).
        # Using the LAST import line breaks files like api_server.py which
        # have a `from routes import ...` at line 2150.
        first_import_end = 0
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                end = getattr(node, 'end_lineno', node.lineno) or node.lineno
                if first_import_end == 0 or end < first_import_end + 3:
                    # First import or consecutive (within 2 lines)
                    if end > first_import_end:
                        first_import_end = end
                else:
                    break  # Stop at gap before scattered import

        insert_at = first_import_end if first_import_end > 0 else 0
        lines.insert(insert_at, import_stmt)
        new_content = '\n'.join(lines)

        try:
            ast.parse(new_content)
        except SyntaxError as e:
            raise RefactoringError(
                f"Syntax error after adding import: {e}",
                category=RefactoringError.IMPORT_FAILED,
                filepath=path,
                details={"line": e.lineno, "msg": e.msg}
            )

        tmppath = path + '.tmp'
        with open(tmppath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        _atomic_replace(tmppath, path)

        return True
