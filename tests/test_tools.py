"""Test tools.py — build_system_prompt localization and git_ops validation."""
import json
import os
import pytest
import config
from tools import ToolRegistry, Tool


class TestToolRegistry:
    def test_tool_registry_init(self):
        tr = ToolRegistry()
        assert tr.lang == "da"
        assert tr.active_tools is None

    def test_set_active_tools(self):
        tr = ToolRegistry()
        tr.set_active_tools(["git_status", "git_log"])
        assert tr.active_tools == ["git_status", "git_log"]

    def test_set_active_tools_none(self):
        tr = ToolRegistry()
        tr.set_active_tools(None)
        assert tr.active_tools is None

    def test_register_tool(self):
        tr = ToolRegistry()
        t = Tool("test_tool", "A test tool", ["arg"], lambda x: x)
        tr.register(t)
        assert "test_tool" in tr.tools

    def test_get_tool_descriptions_all(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "Show git status", [], lambda: ""))
        tr.register(Tool("git_log", "Show git log", ["count"], lambda c: ""))
        desc = tr.get_tool_descriptions()
        assert "git_status" in desc
        assert "git_log" in desc
        assert "Show git status" in desc

    def test_get_tool_descriptions_filtered(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "Show git status", [], lambda: ""))
        tr.register(Tool("git_log", "Show git log", ["count"], lambda c: ""))
        tr.register(Tool("git_commit", "Commit changes", ["msg"], lambda m: ""))
        tr.set_active_tools(["git_status"])
        desc = tr.get_tool_descriptions()
        assert "git_status" in desc
        assert "git_log" not in desc
        assert "git_commit" not in desc


class TestBuildSystemPrompt:
    def _assert_prompt(self, prompt, has_tools=True):
        import config
        if config.NATIVE_TOOLS and has_tools:
            assert "<<<TOOL>>>" not in prompt, "Native tools should NOT have marker instructions"
            assert "<<<DONE>>>" not in prompt, "Native tools should NOT have marker instructions"
        else:
            assert "<<<DONE>>>" in prompt

    def test_build_system_prompt_da(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "Show git status", [], lambda: ""))
        tr.lang = "da"
        prompt = tr.build_system_prompt("Test task")
        self._assert_prompt(prompt)
        assert "git_status" in prompt
        assert "Test task" in prompt

    def test_build_system_prompt_en(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "Show git status", [], lambda: ""))
        tr.lang = "en"
        prompt = tr.build_system_prompt("Test task")
        self._assert_prompt(prompt)
        assert "Show git status" in prompt

    def test_build_system_prompt_es(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "Show git status", [], lambda: ""))
        tr.lang = "es"
        prompt = tr.build_system_prompt("Test task")
        if not config.NATIVE_TOOLS:
            assert "Ejemplo" in prompt

    def test_build_system_prompt_zh(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "Show git status", [], lambda: ""))
        tr.lang = "zh"
        prompt = tr.build_system_prompt("Test task")
        if not config.NATIVE_TOOLS:
            assert "示例" in prompt

    def test_build_system_prompt_with_no_tools(self):
        tr = ToolRegistry()
        tr.lang = "da"
        tr.active_tools = []
        prompt = tr.build_system_prompt("Test task")
        assert "<<<TOOL>>>" not in prompt
        assert "<<<DONE>>>" in prompt
        assert "KUN med" in prompt

    def test_build_system_prompt_error_marker_replaced(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "Show git status", [], lambda: ""))
        tr.lang = "da"
        prompt = tr.build_system_prompt("Test task")
        assert "{ERROR_MARKER}" not in prompt

    def test_build_system_prompt_markers_consistent(self):
        import config
        for lang_code in ["da", "en", "es", "zh"]:
            tr = ToolRegistry()
            tr.register(Tool("git_status", "status", [], lambda: ""))
            tr.lang = lang_code
            prompt = tr.build_system_prompt("task")
            self._assert_prompt(prompt, has_tools=True)


class TestToolParsing:
    def test_strip_markers(self):
        tr = ToolRegistry()
        text = "Some <<<TOOL>>>text<<<END>>> more"
        result = tr.strip_markers(text)
        assert "<<<TOOL>>>" not in result
        assert "<<<END>>>" not in result
        assert "text" in result

    def test_parse_tool_call(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "Show git status", [], lambda: ""))
        response = '<<<TOOL>>>{"tool":"git_status","args":{}}<<<END>>>'
        result = tr.parse_response(response)
        assert result["type"] == "tool"
        assert result["tool"] == "git_status"
        assert result["args"] == {}

    def test_parse_done_call(self):
        tr = ToolRegistry()
        response = '<<<DONE>>>{"result":"Task completed"}<<<END>>>'
        result = tr.parse_response(response)
        assert result["type"] == "done"
        assert "Task completed" in result["result"]

    def test_parse_plain_text(self):
        tr = ToolRegistry()
        response = "This is just plain text"
        result = tr.parse_response(response)
        assert result["type"] == "text"
        assert result["text"] == "This is just plain text"

    def test_parse_invalid_json(self):
        tr = ToolRegistry()
        tr.lang = "da"
        response = '<<<TOOL>>>not valid json<<<END>>>'
        result = tr.parse_response(response)
        assert result["type"] == "error"
        assert "Ugyldigt JSON" in result["message"]

    def test_parse_extra_trailing_brace(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "status", [], lambda: ""))
        response = '<<<TOOL>>>{"tool":"git_status","args":{}}}<<<END>>>'
        result = tr.parse_response(response)
        assert result["type"] == "tool"
        assert result["tool"] == "git_status"

    def test_parse_extra_trailing_braces(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "status", [], lambda: ""))
        response = '<<<TOOL>>>{"tool":"git_status","args":{}}}<<<END>>>'
        result = tr.parse_response(response)
        assert result["type"] == "tool"
        assert result["tool"] == "git_status"

    def test_parse_extra_braces_nested(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "status", [], lambda: ""))
        response = '<<<TOOL>>>{"tool":"git_status","args":{"path":"{test}"}}}<<<END>>>'
        result = tr.parse_response(response)
        assert result["type"] == "tool"
        assert result["tool"] == "git_status"

    def test_parse_removes_think_tags(self):
        tr = ToolRegistry()
        response = "<think> thinking <result> <<<TOOL>>>{\"tool\":\"git_status\",\"args\":{}}<<<END>>>"
        result = tr.parse_response(response)
        assert result["type"] == "tool"
        assert "<think>" not in str(result)

    def test_parse_removes_code_blocks(self):
        tr = ToolRegistry()
        response = "```python\nsome code\n```\n<<<TOOL>>>{\"tool\":\"git_status\",\"args\":{}}<<<END>>>"
        result = tr.parse_response(response)
        assert result["type"] == "tool"


class TestToolExecution:
    def test_execute_unknown_tool(self):
        tr = ToolRegistry()
        tr.lang = "da"
        result = tr.execute("nonexistent_tool", {})
        assert result["success"] is False
        assert "Ukendt værktøj" in result["error"]

    def test_execute_blocked_tool(self):
        tr = ToolRegistry()
        tr.register(Tool("git_commit", "Commit", ["msg"], lambda m: "ok"))
        tr.set_active_tools(["git_status"])
        tr.lang = "da"
        result = tr.execute("git_commit", {"msg": "test"})
        assert result["success"] is False
        assert "ikke tilgængelig" in result["error"]

    def test_execute_valid_tool(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "Show status", [], lambda: "OK"))
        result = tr.execute("git_status", {})
        assert result["success"] is True
        assert result["result"] == "OK"

    def test_execute_tool_with_args(self):
        tr = ToolRegistry()
        tr.register(Tool("git_commit", "Commit", ["msg"], lambda msg: f"committed: {msg}"))
        result = tr.execute("git_commit", {"msg": "test message"})
        assert result["success"] is True
        assert "test message" in result["result"]


class TestRouteMismatch:
    def test_extract_fetch_urls(self, tmp_path):
        from git_ops import _extract_urls, _check_route_mismatch
        html = '''<script>fetch('/api/data'); fetch("/api/other")</script>'''
        h = tmp_path / "index.html"
        h.write_text(html, encoding='utf-8')
        urls = _extract_urls(html, '.html')
        assert '/api/data' in urls
        assert '/api/other' in urls

    def test_extract_action_urls(self, tmp_path):
        from git_ops import _extract_urls
        html = '''<form action="/submit" method="post">'''
        urls = _extract_urls(html, '.html')
        assert '/submit' in urls

    def test_extract_js_fetch(self, tmp_path):
        from git_ops import _extract_urls
        js = '''fetch('/js/data'); axios.get('/api/get'); axios.post('/api/post'); axios.put('/api/put'); axios.delete('/api/delete')'''
        urls = _extract_urls(js, '.js')
        assert '/js/data' in urls
        assert '/api/get' in urls
        assert '/api/post' in urls
        assert '/api/put' in urls
        assert '/api/delete' in urls

    def test_no_mismatch(self, tmp_path):
        from git_ops import _check_route_mismatch
        html = '<script>fetch("/api/data")</script>'
        py = '@app.route("/api/data")\ndef data(): pass'
        d = tmp_path / "app"
        p = d / "app.py"
        h = d / "index.html"
        d.mkdir()
        h.write_text(html, encoding='utf-8')
        p.write_text(py, encoding='utf-8')
        mismatched = _check_route_mismatch(str(h), '.py')
        assert mismatched == []

    def test_mismatch_detected(self, tmp_path):
        from git_ops import _check_route_mismatch
        html = '<script>fetch("/api/data"); fetch("/api/missing")</script>'
        py = '@app.route("/api/data")\ndef data(): pass'
        d = tmp_path / "app"
        p = d / "app.py"
        h = d / "index.html"
        d.mkdir()
        h.write_text(html, encoding='utf-8')
        p.write_text(py, encoding='utf-8')
        mismatched = _check_route_mismatch(str(h), '.py')
        assert mismatched == ['/api/missing']

    def test_parameterized_route_match(self, tmp_path):
        from git_ops import _check_route_mismatch
        html = '<script>fetch("/edit/abc"); fetch("/delete/123")</script>'
        py = '@app.route("/edit/<key>")\ndef edit(key): pass\n@app.route("/delete/<int:id>")\ndef delete(id): pass'
        d = tmp_path / "app"
        p = d / "app.py"
        h = d / "index.html"
        d.mkdir()
        h.write_text(html, encoding='utf-8')
        p.write_text(py, encoding='utf-8')
        mismatched = _check_route_mismatch(str(h), '.py')
        assert mismatched == []

    def test_flask_template_struct(self, tmp_path):
        from git_ops import _check_route_mismatch
        html = '<script>fetch("/api/data"); fetch("/api/missing")</script>'
        py = '@app.route("/api/data")\ndef data(): pass'
        d = tmp_path / "app"
        t = d / "templates"
        t.mkdir(parents=True)
        p = d / "app.py"
        h = t / "index.html"
        h.write_text(html, encoding='utf-8')
        p.write_text(py, encoding='utf-8')
        mismatched = _check_route_mismatch(str(h), '.py')
        assert mismatched == ['/api/missing']

    def test_reverse_py_to_html(self, tmp_path):
        from git_ops import _check_route_mismatch
        py = '@bp.route("/api/data")\ndef data(): pass'
        html = '<script>fetch("/api/data"); fetch("/api/missing")</script>'
        p = tmp_path / "app.py"
        h = tmp_path / "index.html"
        p.write_text(py, encoding='utf-8')
        h.write_text(html, encoding='utf-8')
        mismatched = _check_route_mismatch(str(p), '.html')
        assert mismatched == ['/api/missing']

    def test_no_other_file_returns_empty(self, tmp_path):
        from git_ops import _check_route_mismatch
        html = '<script>fetch("/api/data")</script>'
        h = tmp_path / "index.html"
        h.write_text(html, encoding='utf-8')
        mismatched = _check_route_mismatch(str(h), '.py')
        assert mismatched == []

    def test_done_without_done_in_url(self, tmp_path):
        from git_ops import _check_route_mismatch
        html = '<script>fetch("/api/DONE")</script>'
        py = '@app.route("/api/DONE")\ndef done(): pass'
        h = tmp_path / "app.py"
        p = tmp_path / "index.html"
        h.write_text(py, encoding='utf-8')
        p.write_text(html, encoding='utf-8')
        mismatched = _check_route_mismatch(str(h), '.html')
        assert mismatched == []

    def test_relative_urls_ignored(self, tmp_path):
        from git_ops import _check_route_mismatch
        html = '<script>fetch("relative/path"); fetch("/absolute/path")</script>'
        py = '@app.route("/absolute/path")\ndef ap(): pass'
        h = tmp_path / "app.py"
        p = tmp_path / "index.html"
        h.write_text(py, encoding='utf-8')
        p.write_text(html, encoding='utf-8')
        mismatched = _check_route_mismatch(str(h), '.html')
        assert mismatched == []

    def test_dep_check_missing(self, tmp_path):
        from git_ops import _check_missing_deps
        py = "import flask\nimport cryptography\nimport os"
        req = tmp_path / "requirements.txt"
        req.write_text("flask==2.3.0\n", encoding='utf-8')
        missing = _check_missing_deps(py, str(req))
        assert 'cryptography' in missing
        assert 'flask' not in missing
        assert 'os' not in missing

    def test_dep_check_no_requirements(self, tmp_path):
        from git_ops import _check_missing_deps
        py = "import flask"
        req = tmp_path / "nonexistent.txt"
        missing = _check_missing_deps(py, str(req))
        assert missing == []

    def test_write_file_route_warning(self, tmp_path):
        from git_ops import write_file
        py = tmp_path / "app.py"
        py.write_text('@app.route("/api/data")\ndef data(): pass\n', encoding='utf-8')
        html = tmp_path / "index.html"
        result = write_file(str(html), '<script>fetch("/api/data"); fetch("/api/missing")</script>')
        assert result.get("route_warnings", {}).get(".py") == ['/api/missing']

    def test_write_file_auto_update_req(self, tmp_path):
        from git_ops import write_file
        req = tmp_path / "requirements.txt"
        req.write_text("flask==2.3.0\n", encoding='utf-8')
        new_py = tmp_path / "script.py"
        result = write_file(str(new_py), "import cryptography\nimport flask")
        assert "cryptography" in result.get("req_updated", [])
        updated = req.read_text(encoding='utf-8')
        assert "cryptography" in updated

    def test_write_file_new_file_creates_successfully(self, tmp_path):
        """write_file creates a new .py file and returns success."""
        from git_ops import write_file
        f = tmp_path / "new_file.py"
        result = write_file(str(f), "x = 1\n")
        assert result["success"] is True
        assert f.read_text(encoding='utf-8') == "x = 1\n"

    def test_write_file_new_non_python_skips_ast_check(self, tmp_path):
        """write_file creates non-.py files without AST validation."""
        from git_ops import write_file
        f = tmp_path / "data.txt"
        result = write_file(str(f), "any content")
        assert result["success"] is True
        assert f.read_text(encoding='utf-8') == "any content"

    def test_write_file_existing_file_rejected_without_overwrite(self, tmp_path):
        """write_file rejects overwriting an existing .py file unless overwrite=True."""
        from git_ops import write_file
        f = tmp_path / "existing.py"
        f.write_text("x = 1\n", encoding='utf-8')
        result = write_file(str(f), "x = 2\n")
        assert result["success"] is False
        assert "Filen findes allerede" in result.get("error", "")
        assert f.read_text(encoding='utf-8') == "x = 1\n"

    def test_write_file_existing_file_overwrite_true_succeeds(self, tmp_path):
        """write_file succeeds with overwrite=True on existing .py file."""
        from git_ops import write_file
        f = tmp_path / "existing.py"
        f.write_text("x = 1\n", encoding='utf-8')
        result = write_file(str(f), "x = 2\n", overwrite=True)
        assert result["success"] is True
        assert f.read_text(encoding='utf-8') == "x = 2\n"

    def test_write_file_error_mentions_edit_file_and_overwrite(self, tmp_path):
        """Error message tells the LLM how to fix it: use edit_file or overwrite=true."""
        from git_ops import write_file
        f = tmp_path / "existing.py"
        f.write_text("x = 1\n", encoding='utf-8')
        result = write_file(str(f), "x = 2\n")
        assert result["success"] is False
        error = result.get("error", "")
        assert "edit_file" in error, f"Error should mention edit_file, got: {error}"
        assert "overwrite" in error, f"Error should mention overwrite, got: {error}"

    def test_write_file_overwrite_preserves_other_files(self, tmp_path):
        """Overwriting one .py file doesn't affect other files."""
        from git_ops import write_file
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_text("x = 1\n", encoding='utf-8')
        b.write_text("y = 2\n", encoding='utf-8')
        result = write_file(str(a), "x = 99\n", overwrite=True)
        assert result["success"] is True
        assert a.read_text(encoding='utf-8') == "x = 99\n"
        assert b.read_text(encoding='utf-8') == "y = 2\n"

    def test_write_file_invalid_python_rejected(self, tmp_path):
        """write_file rejects .py files with invalid Python syntax."""
        from git_ops import write_file
        f = tmp_path / "bad.py"
        result = write_file(str(f), "this is not valid python @@")
        assert result["success"] is False
        assert "Syntaksfejl" in result.get("error", "")

    def test_write_file_non_python_skips_syntax_check(self, tmp_path):
        """write_file accepts non-.py files even with invalid syntax."""
        from git_ops import write_file
        f = tmp_path / "template.html"
        result = write_file(str(f), "<html>{{ invalid python }}</html>")
        assert result["success"] is True


class TestEditFile:
    def test_edit_file_basic(self, tmp_path):
        from git_ops import edit_file
        f = tmp_path / "test.py"
        f.write_text("x = 1\ny = 2\nz = 3\n", encoding='utf-8')
        result = edit_file(str(f), "y = 2", "y = 99")
        assert result["success"] is True
        assert f.read_text(encoding='utf-8') == "x = 1\ny = 99\nz = 3\n"

    def test_edit_file_not_found(self, tmp_path):
        from git_ops import edit_file
        f = tmp_path / "test.py"
        f.write_text("x = 1\n", encoding='utf-8')
        result = edit_file(str(f), "nonexistent", "new")
        assert result["success"] is False
        assert "ikke fundet" in result["error"]

    def test_edit_file_multiple_matches(self, tmp_path):
        from git_ops import edit_file
        f = tmp_path / "test.py"
        f.write_text("a = 1\na = 2\na = 3\n", encoding='utf-8')
        result = edit_file(str(f), "a =", "b =")
        assert result["success"] is False
        assert "fundet 3 gange" in result["error"]

    def test_edit_file_syntax_check_fails(self, tmp_path):
        from git_ops import edit_file
        f = tmp_path / "test.py"
        f.write_text("x = 1\n", encoding='utf-8')
        result = edit_file(str(f), "x = 1", "x = 1 broken syntax {{{")
        assert result["success"] is False
        assert "Syntaksfejl" in result["error"]
        # File should NOT be modified
        assert f.read_text(encoding='utf-8') == "x = 1\n"

    def test_edit_file_nonexistent_path(self, tmp_path):
        from git_ops import edit_file
        result = edit_file(str(tmp_path / "nonexistent.py"), "old", "new")
        assert result["success"] is False
        assert "findes ikke" in result["error"]

    def test_edit_file_non_py(self, tmp_path):
        from git_ops import edit_file
        f = tmp_path / "test.txt"
        f.write_text("hello world\n", encoding='utf-8')
        result = edit_file(str(f), "hello", "goodbye")
        assert result["success"] is True
        assert f.read_text(encoding='utf-8') == "goodbye world\n"

    def test_edit_file_route_warning(self, tmp_path):
        from git_ops import edit_file
        py = tmp_path / "app.py"
        py.write_text('@app.route("/api/data")\ndef data(): pass\n', encoding='utf-8')
        html = tmp_path / "index.html"
        html.write_text('<script>fetch("/api/data"); fetch("/api/missing")</script>', encoding='utf-8')
        result = edit_file(str(html), "/api/missing", "/api/data")
        assert result["success"] is True

    def test_edit_file_newline_normalization(self, tmp_path):
        from git_ops import edit_file
        f = tmp_path / "test.py"
        f.write_bytes("x = 1\r\ny = 2\r\nz = 3\r\n".encode('utf-8'))
        result = edit_file(str(f), "y = 2\nz = 3", "y = 99\nz = 99")
        assert result["success"] is True
        assert f.read_bytes().decode('utf-8') == "x = 1\r\ny = 99\r\nz = 99\r\n"


class TestListFiles:
    def test_list_files_basic(self, tmp_path):
        from git_ops import list_files
        (tmp_path / "a.py").write_text("x=1", encoding='utf-8')
        (tmp_path / "b.py").write_text("y=2", encoding='utf-8')
        (tmp_path / "c.txt").write_text("hello", encoding='utf-8')
        result = list_files(str(tmp_path))
        assert result["success"] is True
        assert result["count"] == 3
        names = [f["file"] for f in result["files"]]
        assert "a.py" in names
        assert "c.txt" in names

    def test_list_files_pattern(self, tmp_path):
        from git_ops import list_files
        (tmp_path / "a.py").write_text("x=1", encoding='utf-8')
        (tmp_path / "b.txt").write_text("hello", encoding='utf-8')
        result = list_files(str(tmp_path), pattern=".py")
        assert result["success"] is True
        assert result["count"] == 1
        assert result["files"][0]["file"] == "a.py"

    def test_list_files_max_depth(self, tmp_path):
        from git_ops import list_files
        import os
        sub = tmp_path / "sub"
        sub.mkdir()
        deep = sub / "deep"
        deep.mkdir()
        (tmp_path / "root.py").write_text("x=1", encoding='utf-8')
        (sub / "mid.py").write_text("y=2", encoding='utf-8')
        (deep / "deep.py").write_text("z=3", encoding='utf-8')
        result = list_files(str(tmp_path), max_depth=1)
        assert result["success"] is True
        names = [f["file"] for f in result["files"]]
        assert "root.py" in names
        assert os.path.join("sub", "mid.py") in names
        assert os.path.join("sub", "deep", "deep.py") not in names

    def test_list_files_nonexistent(self):
        from git_ops import list_files
        result = list_files("/nonexistent/path")
        assert result["success"] is False


class TestRemoveUnusedImports:
    def test_remove_single_unused(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("import os\nimport sys\n\nx = os.path.join('a', 'b')\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 1
        assert "import sys" in result["removed"][0]
        content = f.read_text(encoding='utf-8')
        assert "import os" in content
        assert "import sys" not in content
        assert "x = os.path.join" in content

    def test_remove_multiple_unused(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("import os\nimport sys\nimport json\n\nx = os.getcwd()\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 2
        content = f.read_text(encoding='utf-8')
        assert "import os" in content
        assert "import sys" not in content
        assert "import json" not in content

    def test_keep_all_used(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("import os\nimport sys\n\nx = os.getcwd()\ny = sys.version\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 0
        assert result["message"] == "Ingen ubrugte imports fundet."
        assert f.read_text(encoding='utf-8') == "import os\nimport sys\n\nx = os.getcwd()\ny = sys.version\n"

    def test_future_import_kept(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("from __future__ import annotations\nimport sys\n\nx = 1\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 1  # sys removed, __future__ kept
        content = f.read_text(encoding='utf-8')
        assert "from __future__ import annotations" in content
        assert "import sys" not in content

    def test_wildcard_import_kept(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("from os import *\nimport sys\n\nx = 1\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 1  # sys removed, wildcard kept
        content = f.read_text(encoding='utf-8')
        assert "from os import *" in content
        assert "import sys" not in content

    def test_all_names_preserves_import(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("import sys\n\n__all__ = ['sys']\nx = 1\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 0  # sys in __all__ → kept

    def test_indirect_reference_kept(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("import os\n\nx = os.path.join('a', 'b')\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 0  # os.name appears in ast.Attribute → kept

    def test_used_in_function_body(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("import json\n\ndef foo():\n    return json.dumps({'a': 1})\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 0

    def test_no_imports(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("x = 1\ny = 2\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 0

    def test_non_py_file_rejected(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.txt"
        f.write_text("import os\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is False
        assert ".py" in result["error"].lower()

    def test_nonexistent_file(self, tmp_path):
        from git_ops import remove_unused_imports
        result = remove_unused_imports(str(tmp_path / "nonexistent.py"))
        assert result["success"] is False
        assert "findes ikke" in result["error"]

    def test_syntax_error_file(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("import os\n\nx = {{{ broken\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is False
        assert "Syntaxfejl" in result["error"]

    def test_multi_line_import_fully_unused(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("from os import (\n    path,\n    walk,\n)\nimport sys\n\nx = 1\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 2
        content = f.read_text(encoding='utf-8')
        assert "from os import" not in content
        assert "import sys" not in content

    def test_multi_line_import_partially_used(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("from os import (\n    path,\n    walk,\n)\n\nx = path.join('a', 'b')\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 0  # `path` is used → whole import kept

    def test_import_as_name(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("import numpy as np\n\nx = np.array([1, 2])\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 0

    def test_import_as_name_unused(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("import numpy as np\n\nx = 1\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 1
        content = f.read_text(encoding='utf-8')
        assert "import numpy" not in content

    def test_from_import_several_one_used(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("from os import path, walk, listdir\n\nx = path.join('a', 'b')\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 0  # `path` is used → whole line kept

    def test_from_import_unused(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("from os import path\nimport sys\n\nx = 1\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 2
        content = f.read_text(encoding='utf-8')
        assert "from os" not in content
        assert "import sys" not in content

    def test_empty_file(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 0

    def test_only_imports_all_unused(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("import os\nimport sys\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 2
        content = f.read_text(encoding='utf-8').strip()
        assert content == ""

    def test_remove_with_blank_line_compression(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("import os\n\n\n\n\nx = 1\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 1
        content = f.read_text(encoding='utf-8')
        assert "import os" not in content
        assert "x = 1" in content

    def test_remove_multiple_blank_line_compression(self, tmp_path):
        from git_ops import remove_unused_imports
        f = tmp_path / "test.py"
        f.write_text("import os\n\n\n\nimport sys\n\n\n\nx = 1\n", encoding='utf-8')
        result = remove_unused_imports(str(f))
        assert result["success"] is True
        assert result["count"] == 2