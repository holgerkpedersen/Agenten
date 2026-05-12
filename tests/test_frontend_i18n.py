"""Test frontend i18n — index.html _() calls and LANG_FALLBACK coverage."""
import pytest
import re
import os


def extract_js_keys(html_content):
    pattern = r'_([\'"])((?:(?!\1)[^\\]|\\.)*)\1'
    matches = re.findall(pattern, html_content)
    keys = [m[1] for m in matches]
    return sorted(set(keys))


def extract_lang_fallback_keys(html_content):
    start = html_content.find("const LANG_FALLBACK = {")
    end = html_content.find("};", start) + 2
    block = html_content[start:end] if start != -1 else ""
    keys = []
    for line in block.split('\n'):
        m = re.match(r"^\s+([a-zA-Z0-9_]+):", line)
        if m:
            keys.append(m.group(1))
    return sorted(set(keys))


class TestFrontendI18n:
    def test_index_html_exists(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        assert os.path.exists(path), "static/index.html not found"

    def test_lang_fallback_defined(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "LANG_FALLBACK" in content

    def test_lang_fallback_has_keys(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "LANG_FALLBACK" in content
        keys = extract_lang_fallback_keys(content)
        assert len(keys) >= 10, f"Expected >=10 keys in LANG_FALLBACK, got {len(keys)}: {keys[:5]}"

    def test_lang_fallback_has_session_keys(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        keys = extract_lang_fallback_keys(content)
        assert "select_session" in keys

    def test_lang_fallback_has_alert_keys(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        keys = extract_lang_fallback_keys(content)
        alert_keys = [k for k in keys if "no_" in k or "error" in k or "alert" in k.lower()]
        assert len(alert_keys) >= 1, f"Found {alert_keys[:5]}"

    def test_lang_fallback_has_template_keys(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        keys = extract_lang_fallback_keys(content)
        assert "load" in keys or "save" in keys

    def test_lang_fallback_has_panel_keys(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        keys = extract_lang_fallback_keys(content)
        panel_keys = [k for k in keys if "tree" in k or "panel" in k or "llm" in k or "log" in k]
        assert len(panel_keys) >= 1, f"Found {panel_keys[:5]}"

    def test_lang_fallback_has_ui_keys(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        keys = extract_lang_fallback_keys(content)
        ui_keys = [k for k in keys if k not in ['N_files_selected_ui', 'N_lines', 'N_tasks', 'active']]
        assert len(ui_keys) >= 10, f"Got {len(ui_keys)} keys: {ui_keys[:5]}"

    def test_underscore_function_defined(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "function _(" in content or "const _ =" in content or "let _ =" in content

    def test_underscore_uses_window_agenten_lang(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "window.AGENTEN_LANG" in content or "AGENTEN_LANG" in content

    def test_lang_select_dropdown_exists(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "langSelect" in content or 'id="langSelect"' in content

    def test_llm_lang_select_exists(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "llmLangSelect" in content or 'id="llmLangSelect"' in content

    def test_update_html_labels_exists(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "updateHtmlLabels" in content

    def test_refresh_sessions_exists(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "refreshSessions" in content

    def test_fetch_translations_on_load(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "/api/lang/" in content

    def test_localStorage_keys_present(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "agenten_lang" in content
        assert "agenten_llm_lang" in content

    def test_no_obvious_syntax_errors_in_underscore_calls(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        pattern = r'_\([\'"][^\'"]*$'
        matches = re.findall(pattern, content)
        assert len(matches) == 0, f"Found unclosed _() calls: {matches[:5]}"