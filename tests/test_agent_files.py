"""Test agent_files.py — file reading, chunking, folder scanning."""
import pytest
import os
from unittest.mock import patch, MagicMock


class TestChunkText:
    def test_chunk_text_single(self):
        from agent_files import chunk_text
        chunks = chunk_text("x" * 100)
        assert len(chunks) == 1

    def test_chunk_text_multiple(self):
        from agent_files import chunk_text, CHUNK_SIZE
        text = "x" * (CHUNK_SIZE * 2 + 500)
        chunks = chunk_text(text)
        assert len(chunks) == 3
        assert len(chunks[0]) == CHUNK_SIZE
        assert len(chunks[1]) == CHUNK_SIZE
        assert len(chunks[2]) == 500

    def test_chunk_text_empty(self):
        from agent_files import chunk_text
        chunks = chunk_text("")
        assert chunks == []


class TestReadFileContent:
    def test_read_file_content_returns_string(self, tmp_path):
        from agent_files import read_file_content
        f = tmp_path / "test.py"
        f.write_text("x = 1", encoding='utf-8')
        agent = MagicMock()
        result = read_file_content(agent, str(f))
        assert result == "x = 1"

    def test_read_file_content_binary_extensions(self, tmp_path):
        from agent_files import read_file_content
        for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.zip', '.exe', '.dll']:
            f = tmp_path / f"test{ext}"
            f.write_bytes(b'\x00\x01')
            agent = MagicMock()
            result = read_file_content(agent, str(f))
            assert result is None, f"{ext} should return None"

    def test_read_file_content_env_excluded(self, tmp_path):
        from agent_files import read_file_content
        env_file = tmp_path / ".env"
        env_file.write_text("GITHUB_TOKEN=secret123", encoding='utf-8')
        agent = MagicMock()
        result = read_file_content(agent, str(env_file))
        assert result is None

    def test_read_file_content_svg_text(self, tmp_path):
        from agent_files import read_file_content
        f = tmp_path / "test.svg"
        f.write_text('<svg><circle cx="50" cy="50" r="40"/></svg>', encoding='utf-8')
        agent = MagicMock()
        result = read_file_content(agent, str(f))
        assert result is not None
        assert '<circle' in result

    def test_read_file_content_pdf_text(self, tmp_path):
        from agent_files import read_file_content
        f = tmp_path / "test.pdf"
        f.write_text('%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj', encoding='utf-8')
        agent = MagicMock()
        result = read_file_content(agent, str(f))
        assert result is not None
        assert '%PDF-' in result

    def test_read_file_content_null_byte_detected(self, tmp_path):
        from agent_files import read_file_content
        f = tmp_path / "sneaky.bin"
        f.write_bytes(b'\x00\x01\x02')
        agent = MagicMock()
        result = read_file_content(agent, str(f))
        assert result is None

    def test_read_file_content_nonexistent(self):
        from agent_files import read_file_content
        agent = MagicMock()
        result = read_file_content(agent, "/nonexistent/path/file.py")
        assert result is None

    def test_read_file_content_truncation(self, tmp_path):
        from agent_files import read_file_content, CHUNK_SIZE
        f = tmp_path / "big.py"
        big_text = "x" * (CHUNK_SIZE + 100)
        f.write_text(big_text, encoding='utf-8')
        agent = MagicMock()
        result = read_file_content(agent, str(f))
        assert len(result) > CHUNK_SIZE  # CHUNK_SIZE + newline + truncated message

    def test_read_file_content_unicode(self, tmp_path):
        from agent_files import read_file_content
        f = tmp_path / "unicode.py"
        f.write_text("print('æøå')", encoding='utf-8')
        agent = MagicMock()
        result = read_file_content(agent, str(f))
        assert 'æøå' in result

    def test_read_file_content_binary_file_exception(self, tmp_path):
        from agent_files import read_file_content
        f = tmp_path / "binary.txt"
        f.write_bytes(b'\x80\x81\x82\xff\xfe')
        agent = MagicMock()
        result = read_file_content(agent, str(f))
        assert result is None


class TestGetSingleFileContext:
    def test_get_single_file_context_finds_py(self, tmp_path):
        from agent_files import get_single_file_context
        f = tmp_path / "api_server.py"
        f.write_text("from flask import Flask", encoding='utf-8')
        agent = MagicMock()
        path, content = get_single_file_context(agent, "analyser api_server.py")
        assert path is not None
        assert "flask" in content

    def test_get_single_file_context_not_found(self):
        from agent_files import get_single_file_context
        agent = MagicMock()
        path, content = get_single_file_context(agent, "analyser nonexistent.py")
        assert path is None
        assert content is None


class TestGetFolderContext:
    def test_get_folder_context_returns_list(self, tmp_path):
        from agent_files import get_folder_context
        f1 = tmp_path / "test1.py"
        f1.write_text("x = 1", encoding='utf-8')
        f2 = tmp_path / "test2.js"
        f2.write_text("console.log(1)", encoding='utf-8')
        agent = MagicMock()
        result = get_folder_context(agent, str(tmp_path))
        assert result is not None
        filenames = [item["filename"] for item in result]
        assert "test1.py" in filenames
        assert "test2.js" in filenames

    def test_get_folder_context_excludes_dirs(self, tmp_path):
        from agent_files import get_folder_context
        excluded_dir = tmp_path / ".git"
        excluded_dir.mkdir()
        (excluded_dir / "config").write_text("git config", encoding='utf-8')
        f = tmp_path / "test.py"
        f.write_text("x = 1", encoding='utf-8')
        agent = MagicMock()
        result = get_folder_context(agent, str(tmp_path))
        filenames = [item["filename"] for item in result]
        assert ".git/config" not in filenames

    def test_get_folder_context_excludes_env_files(self, tmp_path):
        from agent_files import get_folder_context
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=123", encoding='utf-8')
        f = tmp_path / "test.py"
        f.write_text("x = 1", encoding='utf-8')
        agent = MagicMock()
        result = get_folder_context(agent, str(tmp_path))
        filenames = [item["filename"] for item in result]
        assert ".env" not in filenames

    def test_get_folder_context_max_files(self, tmp_path):
        from agent_files import get_folder_context, FOLDER_SCAN_MAX_FILES
        for i in range(30):
            (tmp_path / f"file{i}.py").write_text(f"x={i}", encoding='utf-8')
        agent = MagicMock()
        result = get_folder_context(agent, str(tmp_path))
        assert len(result) <= FOLDER_SCAN_MAX_FILES

    def test_get_folder_context_max_depth(self, tmp_path):
        from agent_files import get_folder_context, FOLDER_SCAN_MAX_DEPTH
        deep_dir = tmp_path
        for i in range(FOLDER_SCAN_MAX_DEPTH + 2):
            deep_dir = deep_dir / f"level{i}"
        deep_dir.mkdir(parents=True)
        (deep_dir / "deep.py").write_text("x=1", encoding='utf-8')
        shallow = tmp_path / "shallow.py"
        shallow.write_text("y=2", encoding='utf-8')
        agent = MagicMock()
        result = get_folder_context(agent, str(tmp_path))
        filenames = [item["filename"] for item in result]
        assert "shallow.py" in filenames

    def test_get_folder_context_no_folders_in_prompt(self):
        from agent_files import get_folder_context
        agent = MagicMock()
        result = get_folder_context(agent, "no folder path here")
        assert result is None


class TestListChunks:
    def test_list_chunks_empty(self):
        from agent_files import list_chunks
        agent = MagicMock()
        agent.file_chunks = {}
        result = list_chunks(agent)
        assert result["success"] is True
        assert result["chunks"] == []

    def test_list_chunks_with_data(self):
        from agent_files import list_chunks
        agent = MagicMock()
        agent.file_chunks = {"file_a.py": ["chunk1"], "file_b.js": ["c1", "c2"]}
        result = list_chunks(agent)
        assert len(result["chunks"]) == 2
        assert result["count"] == 2


class TestReadChunk:
    def test_read_chunk_valid(self):
        from agent_files import read_chunk
        agent = MagicMock()
        agent.file_chunks = {"file_test.py": ["chunk1", "chunk2"]}
        result = read_chunk(agent, "test.py", 1)
        assert result["success"] is True
        assert result["content"] == "chunk1"

    def test_read_chunk_index_2(self):
        from agent_files import read_chunk
        agent = MagicMock()
        agent.file_chunks = {"file_test.py": ["c1", "c2"]}
        result = read_chunk(agent, "test.py", 2)
        assert result["content"] == "c2"

    def test_read_chunk_invalid_index(self):
        from agent_files import read_chunk
        agent = MagicMock()
        agent.file_chunks = {"file_test.py": ["c1"]}
        result = read_chunk(agent, "test.py", 5)
        assert result["success"] is False

    def test_read_chunk_unknown_file(self):
        from agent_files import read_chunk
        agent = MagicMock()
        agent.file_chunks = {}
        result = read_chunk(agent, "unknown.py", 1)
        assert result["success"] is False

    def test_read_chunk_prefixed_key(self):
        from agent_files import read_chunk
        agent = MagicMock()
        agent.file_chunks = {"file_test.py": ["c1"]}
        result = read_chunk(agent, "file_test.py", 1)
        assert result["success"] is True


class TestLocateCode:
    def test_find_function_by_name(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("class Foo:\n    def bar(self):\n        pass\n\ndef baz():\n    pass\n", encoding='utf-8')
        r = locate_code(str(f), name="baz")
        assert r["success"] is True
        assert r["name"] == "baz"
        assert r["type"] == "function"

    def test_find_class_by_name(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("class Foo:\n    def bar(self):\n        pass\n", encoding='utf-8')
        r = locate_code(str(f), name="Foo")
        assert r["success"] is True
        assert r["name"] == "Foo"
        assert r["type"] == "class"

    def test_find_method_by_name(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("class Foo:\n    def bar(self):\n        pass\n", encoding='utf-8')
        r = locate_code(str(f), name="Foo.bar")
        assert r["success"] is True
        assert r["name"] == "Foo.bar"
        assert r["type"] == "method"

    def test_find_enclosing_function_by_line(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    x = 1\n    y = 2\n\ndef bar():\n    pass\n", encoding='utf-8')
        r = locate_code(str(f), line_no=3)
        assert r["success"] is True
        assert r["name"] == "foo"
        assert r["type"] == "function"

    def test_find_enclosing_method_by_line(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("class Foo:\n    def bar(self):\n        x = 1\n        y = 2\n", encoding='utf-8')
        r = locate_code(str(f), line_no=3)
        assert r["success"] is True
        assert r["name"] == "Foo.bar"
        assert r["type"] == "method"

    def test_find_nested_function_by_line(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("def outer():\n    def inner():\n        x = 1\n    return inner\n", encoding='utf-8')
        r = locate_code(str(f), line_no=3)
        assert r["success"] is True
        assert r["name"] == "inner"

    def test_module_level_code_no_enclosing(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("x = 1\ny = 2\n\ndef foo():\n    pass\n", encoding='utf-8')
        r = locate_code(str(f), line_no=1)
        assert r["success"] is True
        assert r["name"] is None
        assert r["type"] == "module"

    def test_line_at_function_start_finds_function(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    pass\n", encoding='utf-8')
        r = locate_code(str(f), line_no=1)
        assert r["success"] is True
        assert r["name"] == "foo"

    def test_async_function(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("import asyncio\n\nasync def fetch():\n    pass\n", encoding='utf-8')
        r = locate_code(str(f), line_no=3)
        assert r["success"] is True
        assert r["name"] == "fetch"

    def test_find_async_function_by_name(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("import asyncio\n\nasync def fetch():\n    pass\n", encoding='utf-8')
        r = locate_code(str(f), name="fetch")
        assert r["success"] is True
        assert r["name"] == "fetch"
        assert r["type"] == "async_function"

    def test_file_not_found(self):
        from agent_files import locate_code
        r = locate_code("nonexistent.py", line_no=10)
        assert r["success"] is False
        assert "File not found" in r["error"]

    def test_symbol_not_found(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    pass\n", encoding='utf-8')
        r = locate_code(str(f), name="nonexistent")
        assert r["success"] is False
        assert "not found" in r["error"]

    def test_syntax_error(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("def foo(", encoding='utf-8')
        r = locate_code(str(f), line_no=1)
        assert r["success"] is False
        assert "Syntax error" in r["error"]

    def test_no_name_or_line(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    pass\n", encoding='utf-8')
        r = locate_code(str(f))
        assert r["success"] is False
        assert "name" in r["error"] or "line_no" in r["error"]

    def test_method_not_found_in_class(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("class Foo:\n    def bar(self):\n        pass\n", encoding='utf-8')
        r = locate_code(str(f), name="Foo.nonexistent")
        assert r["success"] is False
        assert "not found" in r["error"]

    def test_line_out_of_range(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    pass\n", encoding='utf-8')
        r = locate_code(str(f), line_no=999)
        assert r["success"] is True
        assert r["name"] is None
        assert r["type"] == "module"

    def test_body_contains_function_content(self, tmp_path):
        from agent_files import locate_code
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    x = 1\n    return x\n", encoding='utf-8')
        r = locate_code(str(f), name="foo")
        assert r["success"] is True
        assert "x = 1" in r["body"]
        assert "return x" in r["body"]


class TestIsSafePath:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.base_dir = str(tmp_path)
        self.inside_file = tmp_path / "inside.py"
        self.inside_file.write_text("x = 1", encoding='utf-8')
        self.sub_dir = tmp_path / "sub"
        self.sub_dir.mkdir()
        self.nested_file = self.sub_dir / "nested.py"
        self.nested_file.write_text("y = 2", encoding='utf-8')

    def _is_safe_path(self, base_dir, target_path):
        from agent_files import _is_safe_path
        return _is_safe_path(base_dir, target_path)

    def test_file_inside_dir(self):
        assert self._is_safe_path(self.base_dir, str(self.inside_file)) is True

    def test_file_outside_dir(self):
        assert self._is_safe_path(self.base_dir, r"C:\Windows\system32\config") is False

    def test_base_dir_itself(self):
        assert self._is_safe_path(self.base_dir, self.base_dir) is True

    def test_nested_subdirectory(self):
        assert self._is_safe_path(self.base_dir, str(self.nested_file)) is True

    def test_path_traversal_dotdot(self):
        malicious = os.path.join(self.base_dir, "..", "..", "Windows", "system32")
        assert self._is_safe_path(self.base_dir, malicious) is False

    def test_path_traversal_encoded(self):
        malicious = os.path.join(self.base_dir, "..", "..", "..", "etc", "passwd")
        assert self._is_safe_path(self.base_dir, malicious) is False

    def test_nonexistent_path_inside(self):
        nonexistent = os.path.join(self.base_dir, "nonexistent", "file.py")
        assert self._is_safe_path(self.base_dir, nonexistent) is True

    def test_nonexistent_path_outside(self):
        nonexistent = r"C:\DoesNotExist\file.py"
        assert self._is_safe_path(self.base_dir, nonexistent) is False

    def test_different_base_dir(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        other_file = other / "other.py"
        other_file.write_text("z = 3", encoding='utf-8')
        assert self._is_safe_path(str(other), str(other_file)) is True
        assert self._is_safe_path(str(other), str(self.inside_file)) is False


class TestIsSafeLocation:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.project_file = tmp_path / "project.py"
        self.project_file.write_text("x = 1", encoding='utf-8')

    def _is_safe_location(self, target_path):
        from agent_files import is_safe_location
        return is_safe_location(target_path)

    @patch('agent_files._SAFE_DIRS', new_callable=set)
    def test_path_in_safe_dirs(self, mock_safe_dirs, tmp_path):
        mock_safe_dirs.add(os.path.realpath(str(tmp_path)))
        safe_file = tmp_path / "safe.py"
        safe_file.write_text("ok", encoding='utf-8')
        assert self._is_safe_location(str(safe_file)) is True

    @patch('agent_files._SAFE_DIRS', new_callable=set)
    def test_path_outside_safe_dirs(self, mock_safe_dirs):
        mock_safe_dirs.add(str(self.project_file.parent))
        assert self._is_safe_location(r"C:\Windows\system32") is False

    @patch('agent_files._SAFE_DIRS', new_callable=set)
    def test_temp_dir_is_safe(self, mock_safe_dirs, tmp_path):
        mock_safe_dirs.add(os.path.realpath(str(tmp_path)))
        inside = tmp_path / "inside.txt"
        inside.write_text("temp", encoding='utf-8')
        assert self._is_safe_location(str(inside)) is True

    @patch('agent_files._SAFE_DIRS', new_callable=set)
    def test_path_traversal_rejected(self, mock_safe_dirs, tmp_path):
        mock_safe_dirs.add(os.path.realpath(str(tmp_path)))
        malicious = os.path.join(str(tmp_path), "..", "..", "Windows", "system32")
        assert self._is_safe_location(malicious) is False

    @patch('agent_files._SAFE_DIRS', new_callable=set)
    def test_nonexistent_path_in_safe_dir(self, mock_safe_dirs, tmp_path):
        mock_safe_dirs.add(os.path.realpath(str(tmp_path)))
        nonexistent = os.path.join(str(tmp_path), "future_dir", "future_file.py")
        assert self._is_safe_location(nonexistent) is True

    @patch('agent_files._SAFE_DIRS', new_callable=set)
    def test_exception_returns_false(self, mock_safe_dirs):
        mock_safe_dirs.add(os.path.realpath("."))
        assert self._is_safe_location(None) is False

    def test_project_root_in_safe_dirs(self):
        import tempfile
        from agent_files import is_safe_location, _SAFE_DIRS
        assert len(_SAFE_DIRS) >= 2

    def test_exports_uploads_in_safe_dirs(self):
        from agent_files import _SAFE_DIRS
        bases = {p for p in _SAFE_DIRS if 'exports' in p or 'uploads' in p}
        assert len(bases) == 2, f"Expected exports/ and uploads/ in _SAFE_DIRS, got: {bases}"


class TestIsSafeLocation:
    def _call(self, target_path):
        from agent_files import is_safe_location
        return is_safe_location(target_path)

    @patch('agent_files._SAFE_DIRS', new_callable=set)
    def test_path_in_safe_returns_true(self, mock_safe_dirs, tmp_path):
        mock_safe_dirs.add(os.path.realpath(str(tmp_path)))
        safe = tmp_path / "safe.txt"
        safe.write_text("ok", encoding='utf-8')
        assert self._call(str(safe)) is True

    @patch('agent_files._SAFE_DIRS', new_callable=set)
    def test_path_outside_returns_false(self, mock_safe_dirs, tmp_path):
        mock_safe_dirs.add(os.path.realpath(str(tmp_path)))
        assert self._call(r"C:\Windows\system32") is False
