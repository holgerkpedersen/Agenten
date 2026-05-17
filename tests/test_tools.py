"""Test tools.py — build_system_prompt localization and git_ops validation."""
import json
import os
import pytest
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
    def test_build_system_prompt_da(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "Show git status", [], lambda: ""))
        tr.lang = "da"
        prompt = tr.build_system_prompt("Test task")
        assert "<<<TOOL>>>" in prompt
        assert "<<<DONE>>>" in prompt
        assert "<<<END>>>" in prompt
        assert "git_status" in prompt
        assert "Test task" in prompt

    def test_build_system_prompt_en(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "Show git status", [], lambda: ""))
        tr.lang = "en"
        prompt = tr.build_system_prompt("Test task")
        assert "<<<TOOL>>>" in prompt
        assert "<<<DONE>>>" in prompt
        assert "Show git status" in prompt

    def test_build_system_prompt_es(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "Show git status", [], lambda: ""))
        tr.lang = "es"
        prompt = tr.build_system_prompt("Test task")
        assert "Ejemplo" in prompt

    def test_build_system_prompt_zh(self):
        tr = ToolRegistry()
        tr.register(Tool("git_status", "Show git status", [], lambda: ""))
        tr.lang = "zh"
        prompt = tr.build_system_prompt("Test task")
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
        for lang_code in ["da", "en", "es", "zh"]:
            tr = ToolRegistry()
            tr.register(Tool("git_status", "status", [], lambda: ""))
            tr.lang = lang_code
            prompt = tr.build_system_prompt("task")
            assert prompt.count("<<<TOOL>>>") >= 1
            assert prompt.count("<<<DONE>>>") >= 1
            assert prompt.count("<<<END>>>") >= 1


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