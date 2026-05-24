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
        for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.svg', '.pdf', '.zip', '.exe', '.dll']:
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
