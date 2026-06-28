"""End-to-end test: batch_extract_symbols on nested functions.

Uses a synthetic source file to verify that nested functions are
detected and converted correctly, and that move_symbol skips
remove/import for converted symbols.
"""
import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from refactoring_engine import RefactoringEngine, _is_nested_function

# Synthetic source with various nested function patterns
TEST_SOURCE = """import os
import sys
from typing import Any

VERSION = "1.0"

def outer_function(x: int) -> int:
    \"\"\"Has a simple nested function (read capture).\"\"\"
    factor = 2
    def inner(y: int) -> int:
        return y * factor + x
    return inner(10)

def callback_creator(items: list) -> list[Any]:
    \"\"\"Has a nested function with for-loop variable capture.\"\"\"
    results = []
    for item in items:
        def process(val: Any) -> str:
            return f"Processed: {val}"
        results.append(process(item))
    return results

def counter_maker(start: int = 0) -> Any:
    \"\"\"Has a stateful closure (nonlocal).\"\"\"
    count = start
    def increment(step: int = 1) -> int:
        nonlocal count
        count += step
        return count
    return increment

def simple_wrapper(msg: str) -> Any:
    \"\"\"Has a nested function with no captured vars.\"\"\"
    def wrapper() -> str:
        return msg
    return wrapper

def no_nesting(x: int) -> int:
    \"\"\"No nested function, just a simple function.\"\"\"
    return x * 2

class Helper:
    def method_with_inner(self, data: list) -> list:
        \"\"\"Method that contains a nested function.\"\"\"
        multiplier = 3
        def inner_method(val: int) -> int:
            return val * multiplier
        return [inner_method(x) for x in data]

    def ordinary_method(self) -> str:
        return "hello"
"""


def _get_nested_symbols(source_code: str) -> list[str]:
    """Find symbols that are nested functions in the source code."""
    import ast
    tree = ast.parse(source_code)
    nested = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _is_nested_function(tree, node):
            nested.append(node.name)
    return sorted(set(nested))


class TestNestedExtractionE2E(unittest.TestCase):

    def test_find_nested_functions_in_source(self):
        nested = _get_nested_symbols(TEST_SOURCE)
        self.assertGreater(len(nested), 0)
        expected = {'inner', 'inner_method', 'increment', 'wrapper'}
        for name in expected:
            self.assertIn(name, nested, f"Expected {name} to be detected as nested")
        print(f"\nNested functions found ({len(nested)}): {nested}")

    def test_batch_extract_nested_to_temp_target(self):
        engine = RefactoringEngine()
        nested = _get_nested_symbols(TEST_SOURCE)
        self.assertGreaterEqual(len(nested), 3)

        with tempfile.TemporaryDirectory() as tmp:
            src_copy = os.path.join(tmp, 'test_source.py')
            with open(src_copy, 'w', encoding='utf-8') as f:
                f.write(TEST_SOURCE)
            target = os.path.join(tmp, 'nested_helpers.py')

            result = engine.batch_extract_symbols(
                source=src_copy,
                symbols=nested,
                target=target,
            )

            self.assertTrue(result.get("success"), f"batch_extract failed: {result.get('error', '')}")
            self.assertGreater(result.get("succeeded", 0), 0)
            self.assertEqual(result["succeeded"], result["total"],
                             f"Not all symbols succeeded: {result.get('results', [])}")

            print(f"\nResult: {result['succeeded']}/{result['total']} succeeded")
            print(f"  Nested converted: {result.get('nested_converted')}")
            print(f"  Stateful: {result.get('stateful_converted')}")
            for r in result.get("results", []):
                if r.get("success"):
                    extra = ""
                    if r.get("nested_function"):
                        extra = f" [nested, captured={r.get('captured_vars', [])}]"
                    if r.get("stateful_closure"):
                        extra = f" [stateful → {r.get('class_name', '?')}]"
                    print(f"  ✅ {r['symbol']}{extra}")
                else:
                    print(f"  ❌ {r['symbol']}: {r.get('error', '?')}")

            # Target must exist and have content
            self.assertTrue(os.path.exists(target))
            with open(target, encoding='utf-8') as f:
                self.assertGreater(len(f.read().strip()), 0)

            # Source must still be valid Python
            import ast
            with open(src_copy, encoding='utf-8') as f:
                ast.parse(f.read().replace('\r\n', '\n'))

    def test_move_symbol_nested_does_not_crash(self):
        """move_symbol must not crash on remove step for nested functions."""
        engine = RefactoringEngine()
        nested = _get_nested_symbols(TEST_SOURCE)
        self.assertTrue(len(nested) > 0)

        with tempfile.TemporaryDirectory() as tmp:
            src_copy = os.path.join(tmp, 'test_source.py')
            with open(src_copy, 'w', encoding='utf-8') as f:
                f.write(TEST_SOURCE)
            target = os.path.join(tmp, 'nested_test.py')

            # Use the first non-stateful nested function
            name = [n for n in nested if n != 'increment'][0]
            result = engine.move_symbol(
                source=src_copy,
                symbol_name=name,
                target=target,
            )

            self.assertTrue(result.get("success"),
                            f"move_symbol failed: {result.get('error', '')}")
            # The result must skip remove + import for converted nested
            if result.get("nested_function"):
                self.assertTrue(result["steps"]["remove"].get("skipped"))
                self.assertTrue(result["steps"]["import"].get("skipped"))


if __name__ == '__main__':
    unittest.main()
