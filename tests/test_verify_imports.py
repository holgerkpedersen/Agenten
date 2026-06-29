"""Test verify_imports — proves it catches the refactoring-missing-import pattern."""
import pytest
import tempfile
import os


def _write_temp_py(dirpath: str, filename: str, content: str) -> str:
    filepath = os.path.join(dirpath, filename)
    os.makedirs(dirpath, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


class TestVerifyMissingImport:
    """Core pattern: symbol is defined in module A, called in module B without import."""

    def test_catches_missing_import(self):
        tmp = tempfile.mkdtemp()
        try:
            _write_temp_py(tmp, "helpers.py", "def my_helper():\n    pass\n")
            _write_temp_py(tmp, "caller.py", "def foo():\n    my_helper()\n")

            from verify_imports import verify_all_imports
            issues = verify_all_imports(tmp)
            assert len(issues) >= 1
            paths = [os.path.basename(f) for f, _ in issues]
            assert "caller.py" in paths
            for _, missing in issues:
                for name, lineno, defined_in in missing:
                    if name == "my_helper":
                        assert lineno == 2
                        assert os.path.basename(defined_in) == "helpers.py"
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_does_not_flag_when_imported(self):
        tmp = tempfile.mkdtemp()
        try:
            _write_temp_py(tmp, "helpers.py", "def my_helper():\n    pass\n")
            _write_temp_py(tmp, "caller.py",
                "from helpers import my_helper\n\ndef foo():\n    my_helper()\n")

            from verify_imports import verify_all_imports
            issues = verify_all_imports(tmp)
            my_helper_issues = [
                (n, l) for _, m in issues for n, l, _ in m if n == "my_helper"
            ]
            assert len(my_helper_issues) == 0
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_does_not_flag_local_definitions(self):
        tmp = tempfile.mkdtemp()
        try:
            _write_temp_py(tmp, "caller.py", "def foo():\n    def inner():\n        pass\n    inner()\n")

            from verify_imports import verify_all_imports
            issues = verify_all_imports(tmp)
            assert len(issues) == 0
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_does_not_flag_builtins(self):
        tmp = tempfile.mkdtemp()
        try:
            _write_temp_py(tmp, "caller.py", "def foo():\n    print('hello')\n    len('abc')\n")

            from verify_imports import verify_all_imports
            issues = verify_all_imports(tmp)
            assert len(issues) == 0
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_does_not_flag_external_imports(self):
        tmp = tempfile.mkdtemp()
        try:
            _write_temp_py(tmp, "caller.py",
                "import os\n\ndef foo():\n    os.path.join('a', 'b')\n")

            from verify_imports import verify_all_imports
            issues = verify_all_imports(tmp)
            assert len(issues) == 0
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_flags_multiple_missing(self):
        tmp = tempfile.mkdtemp()
        try:
            _write_temp_py(tmp, "helpers.py",
                "def helper_a():\n    pass\n\ndef helper_b():\n    pass\n")
            _write_temp_py(tmp, "caller.py",
                "def foo():\n    helper_a()\n    helper_b()\n")

            from verify_imports import verify_all_imports
            issues = verify_all_imports(tmp)
            assert len(issues) >= 1
            names = [n for _, m in issues for n, _, _ in m]
            assert "helper_a" in names
            assert "helper_b" in names
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_does_not_flag_if_defined_in_same_file(self):
        tmp = tempfile.mkdtemp()
        try:
            _write_temp_py(tmp, "caller.py",
                "def my_helper():\n    pass\n\ndef foo():\n    my_helper()\n")

            from verify_imports import verify_all_imports
            issues = verify_all_imports(tmp)
            assert len(issues) == 0
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_skips_tests_uploads_dirs(self):
        """Verifies scanner excludes test/upload directories."""
        tmp = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmp, "tests"), exist_ok=True)
            _write_temp_py(os.path.join(tmp, "tests"), "test_stuff.py",
                "def test_foo():\n    unknown_helper()\n")

            from verify_imports import verify_all_imports
            issues = verify_all_imports(tmp)
            # Should not flag test_stuff.py since tests/ is excluded
            for filepath, _ in issues:
                assert "tests" not in filepath
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
