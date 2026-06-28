"""End-to-end test: batch_extract_symbols on all nested functions in api_server.py.

Verifies that nested functions are detected and converted correctly,
and that move_symbol skips remove/import for converted symbols.
"""
import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from refactoring_engine import RefactoringEngine, _is_nested_function

API_SERVER = os.path.join(os.path.dirname(__file__), '..', 'api_server.py')


def _get_nested_symbols(filepath: str) -> list[str]:
    """Find symbols that are nested functions in the file."""
    import ast
    with open(filepath, encoding='utf-8') as f:
        content = f.read().replace('\r\n', '\n').replace('\r', '\n')
    tree = ast.parse(content)
    nested = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _is_nested_function(tree, node):
            nested.append(node.name)
    return sorted(set(nested))


class TestNestedExtractionE2E(unittest.TestCase):

    def test_find_nested_functions_in_api_server(self):
        nested = _get_nested_symbols(API_SERVER)
        self.assertGreater(len(nested), 0)
        print(f"\nNested functions in api_server.py ({len(nested)}): {nested}")

    def test_batch_extract_nested_to_temp_target(self):
        engine = RefactoringEngine()
        nested = _get_nested_symbols(API_SERVER)
        if len(nested) < 3:
            raise unittest.SkipTest(f"Too few nested ({len(nested)})")

        with tempfile.TemporaryDirectory() as tmp:
            src_copy = os.path.join(tmp, 'api_server.py')
            shutil.copy2(API_SERVER, src_copy)
            target = os.path.join(tmp, 'nested_helpers.py')

            result = engine.batch_extract_symbols(
                source=src_copy,
                symbols=nested,
                target=target,
            )

            self.assertTrue(result.get("success"))
            self.assertGreater(result.get("succeeded", 0), 0)

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
        nested = _get_nested_symbols(API_SERVER)
        if not nested:
            raise unittest.SkipTest("No nested functions")

        name = nested[0]
        with tempfile.TemporaryDirectory() as tmp:
            src_copy = os.path.join(tmp, 'api_server.py')
            shutil.copy2(API_SERVER, src_copy)
            target = os.path.join(tmp, 'nested_test.py')

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
