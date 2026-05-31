#!/usr/bin/env python3
"""Add missing docstrings to all function/class defs in Python files.

Usage:
    python scripts/add_docstrings.py              # modify files
    python scripts/add_docstrings.py --dry-run     # preview only
"""

import ast
import glob
import os
import re
import sys


def has_docstring(body):
    return (body and
            isinstance(body[0], ast.Expr) and
            isinstance(body[0].value, ast.Constant) and
            isinstance(body[0].value.value, str))


DUNDER_DESCRIPTIONS = {
    "__init__": "Initialize the instance",
    "__str__": "Return a string representation",
    "__repr__": "Return a string representation",
    "__len__": "Return the length",
    "__iter__": "Iterate over items",
    "__next__": "Return the next item",
    "__contains__": "Check if an item is contained",
    "__getitem__": "Get an item by key",
    "__setitem__": "Set an item by key",
    "__delitem__": "Delete an item by key",
    "__enter__": "Enter the context manager",
    "__exit__": "Exit the context manager",
    "__call__": "Call the instance",
    "__eq__": "Check equality",
    "__ne__": "Check inequality",
    "__lt__": "Check less than",
    "__le__": "Check less than or equal",
    "__gt__": "Check greater than",
    "__ge__": "Check greater than or equal",
    "__hash__": "Return a hash value",
    "__bool__": "Return a boolean value",
    "__int__": "Convert to integer",
    "__float__": "Convert to float",
    "__del__": "Clean up resources",
    "__new__": "Create a new instance",
}


def snake_to_sentence(name):
    """Convert a snake_case or camelCase name to a readable sentence."""
    # Handle dunder methods
    if name in DUNDER_DESCRIPTIONS:
        return DUNDER_DESCRIPTIONS[name]
    name = re.sub(r'^_+', '', name)
    name = name.replace('_', ' ')
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    name = re.sub(r'\bi\b', 'I', name)
    result = name.strip().lower()
    if not result:
        result = name
    return result


def format_annotation(ann):
    try:
        return ast.unparse(ann)
    except Exception:
        return ""


def _first_line_of_body(node):
    """Get meaningful first line of a function/class body for context."""
    for stmt in node.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # skip docstring itself
        try:
            return ast.unparse(stmt)
        except Exception:
            return ""
    return ""


def generate_function_docstring(node):
    """Generate a Google-style docstring for a function/method."""
    name = node.name
    desc = snake_to_sentence(name)

    args = []
    for a in node.args.args:
        if a.arg in ('self', 'cls'):
            continue
        ann = format_annotation(a.annotation) if a.annotation else ''
        args.append((a.arg, ann))

    for a in node.args.kwonlyargs:
        ann = format_annotation(a.annotation) if a.annotation else ''
        args.append((a.arg, ann, True))

    if node.args.vararg:
        ann = format_annotation(node.args.vararg.annotation) if node.args.vararg.annotation else ''
        args.append((f"*{node.args.vararg.arg}", ann))

    if node.args.kwarg:
        ann = format_annotation(node.args.kwarg.annotation) if node.args.kwarg.annotation else ''
        args.append((f"**{node.args.kwarg.arg}", ann))

    has_yield = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(node))

    lines = [f"{desc}."]

    if args:
        lines.append("")
        lines.append("Args:")
        for arg_info in args:
            name_str = arg_info[0]
            ann = arg_info[1]
            if ann:
                lines.append(f"    {name_str} ({ann}):")
            else:
                lines.append(f"    {name_str}:")

    if node.returns:
        lines.append("")
        lines.append("Returns:")
        lines.append(f"    {format_annotation(node.returns)}")
    elif has_yield:
        lines.append("")
        lines.append("Yields:")
        lines.append("    ...")

    return "\n".join(lines)


def generate_class_docstring(node):
    """Generate a docstring for a class definition."""
    name = node.name
    desc = snake_to_sentence(name)
    bases = [format_annotation(b) for b in node.bases if format_annotation(b)]

    lines = [f"{desc}."]
    if bases:
        lines.append("")
        lines.append(f"Extends: {', '.join(bases)}")

    return "\n".join(lines)


def collect_missing_docstrings(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    nodes = []

    def walk_body(body):
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not has_docstring(stmt.body):
                    nodes.append(stmt)
                walk_body(stmt.body)
            elif isinstance(stmt, ast.ClassDef):
                if not has_docstring(stmt.body):
                    nodes.append(stmt)
                walk_body(stmt.body)

    walk_body(tree.body)
    return nodes


def add_docstrings_to_source(source, nodes):
    lines = source.split('\n')

    # Process in reverse line order to preserve indices
    nodes.sort(key=lambda n: n.body[0].lineno if n.body else n.lineno, reverse=True)

    result_lines = list(lines)

    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = generate_function_docstring(node)
        elif isinstance(node, ast.ClassDef):
            doc = generate_class_docstring(node)
        else:
            continue

        if not doc:
            continue

        if not node.body:
            continue  # shouldn't happen in valid Python

        if isinstance(node, ast.ClassDef):
            # For classes: insert at class body start (right after class header)
            # Use class lineno to compute body indentation
            class_header = result_lines[node.lineno - 1]
            class_indent = re.match(r'^(\s*)', class_header).group(1)
            body_indent = class_indent + "    "

            # Find insertion point: first line after class header colon
            insert_idx = node.lineno  # 0-indexed line after class header
            # Skip blank lines between class header and first body statement
            while insert_idx < len(result_lines) and not result_lines[insert_idx].strip():
                insert_idx += 1
        else:
            # For functions: insert before first body statement
            insert_idx = node.body[0].lineno - 1
            body_line = result_lines[insert_idx]
            body_indent = re.match(r'^(\s*)', body_line).group(1)

        doc_lines = doc.split('\n')
        formatted = []
        for i, dl in enumerate(doc_lines):
            if i == 0:
                formatted.append(f'{body_indent}"""{dl}')
            else:
                formatted.append(f'{body_indent}{dl}')
        formatted[-1] += '"""'

        for dl in reversed(formatted):
            result_lines.insert(insert_idx, dl)

    return '\n'.join(result_lines)


def process_file(filepath, dry_run=False):
    with open(filepath, 'r', encoding='utf-8') as f:
        source = f.read()

    basename = os.path.basename(filepath)

    nodes = collect_missing_docstrings(source)
    if nodes is None:
        print(f"  SKIP Syntax error: {basename}")
        return 0

    if not nodes:
        print(f"  OK All documented: {basename}")
        return 0

    new_source = add_docstrings_to_source(source, nodes)

    if new_source == source:
        print(f"  - No changes: {basename}")
        return 0

    if not dry_run:
        if not new_source.endswith('\n'):
            new_source += '\n'
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_source)

    print(f"  + {len(nodes)} docstrings: {basename}")
    return len(nodes)


def main():
    # Determine project root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(script_dir)

    dry_run = '--dry-run' in sys.argv

    py_files = sorted(glob.glob(os.path.join(root, '*.py')))
    # Skip test files at root
    py_files = [f for f in py_files if not os.path.basename(f).startswith('test_')]

    print(f"Scanning {len(py_files)} Python files in {root}")
    if dry_run:
        print("  (dry-run mode - no files modified)")

    total = 0
    for filepath in py_files:
        n = process_file(filepath, dry_run=dry_run)
        total += n

    print(f"\n{'Would add' if dry_run else 'Added'} {total} docstrings across {len(py_files)} files")


if __name__ == '__main__':
    main()
