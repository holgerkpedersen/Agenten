"""Tests for agent_autoresearch.py — classification, dedup, research loop."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.active_template = "issue_handler"
    agent._session_id = "test-session-123"
    agent._tool_log = []
    agent.tool_registry.active_tools = ["update_issue_status", "read_issue", "done"]
    return agent


@pytest.fixture
def mock_agent_readonly():
    agent = MagicMock()
    agent.active_template = "kodeanalyse"
    agent._session_id = "test-session-123"
    agent._tool_log = []
    agent.tool_registry.active_tools = [
        "read_location", "read_chunk", "list_chunks",
        "list_files", "list_symbols", "locate", "done",
    ]
    return agent


@pytest.fixture
def mock_agent_fix():
    agent = MagicMock()
    agent.active_template = "issue_handler"
    agent._session_id = "test-session-123"
    agent._tool_log = []
    agent.tool_registry.active_tools = [
        "read_issue", "read_location", "locate",
        "edit_file", "write_file", "run_tests", "done",
    ]
    return agent


@pytest.fixture
def mock_task_node():
    node = MagicMock()
    node.name = "Luk Issue"
    node.status = "failed"
    return node


class TestClassifyFailure:
    def test_missing_tool(self, mock_agent, mock_task_node):
        from agent_autoresearch import classify_failure
        called_tools = {"read_issue{}": 1}
        ftype, evidence = classify_failure(
            mock_task_node, called_tools, [], "tekst", mock_agent)
        assert ftype == "missing_tool"
        assert "update_issue_status" in evidence.get("uncalled", [])

    def test_tool_failed(self, mock_agent, mock_task_node):
        from agent_autoresearch import classify_failure
        called_tools = {"update_issue_status{'issue_id': 'X'}": 1}
        tool_log = [
            {"tool": "update_issue_status", "success": False,
             "error": "Issue 'X' not found.", "args": {"issue_id": "X"}},
        ]
        ftype, evidence = classify_failure(
            mock_task_node, called_tools, tool_log, "", mock_agent)
        assert ftype == "tool_failed"
        assert evidence.get("tool") == "update_issue_status"

    def test_read_loop(self, mock_agent_readonly, mock_task_node):
        from agent_autoresearch import classify_failure
        called_tools = {"read_location{}": 1}
        tool_log = [{"tool": "read_location", "success": True}] * 6
        ftype, evidence = classify_failure(
            mock_task_node, called_tools, tool_log, "", mock_agent_readonly)
        assert ftype == "read_loop"
        assert evidence.get("consecutive_reads", 0) >= 5

    def test_short_output(self, mock_agent_readonly, mock_task_node):
        from agent_autoresearch import classify_failure
        ftype, evidence = classify_failure(
            mock_task_node, {}, [], "kort", mock_agent_readonly)
        assert ftype == "short_output"
        assert evidence.get("response_length") == 4

    def test_unknown_failure(self, mock_agent_readonly, mock_task_node):
        from agent_autoresearch import classify_failure
        called_tools = {"locate{}": 1}
        tool_log = [{"tool": "locate", "success": True}]
        ftype, evidence = classify_failure(
            mock_task_node, called_tools, tool_log,
            "lang tekst " * 20, mock_agent_readonly)
        assert ftype == "unknown"

    def test_status_not_checked(self, mock_agent_readonly, mock_task_node):
        from agent_autoresearch import classify_failure
        mock_task_node.status = "done"
        ftype, _ = classify_failure(mock_task_node, {}, [], "hej", mock_agent_readonly)
        assert ftype == "short_output"

    def test_incomplete_ekstraher(self, tmp_path, monkeypatch):
        from agent_autoresearch import classify_failure
        agent = MagicMock()
        agent.active_template = "refactor"
        agent.tool_registry.active_tools = ["batch_extract_symbols", "verify_refactor"]
        task_node = MagicMock()
        task_node.name = "Ekstraher"
        monkeypatch.chdir(tmp_path)
        plan = tmp_path / "refactor_plan.md"
        plan.write_text(
            "## Module: modul_a.py\n**Symboler (2):** sym1, sym2\n\n"
            "## Module: modul_b.py\n**Symboler (2):** sym3, sym4\n\n"
            "## Module: modul_c.py\n**Symboler (1):** sym5\n",
            encoding="utf-8"
        )
        # Only create 2 of 3 modules
        (tmp_path / "modul_a.py").write_text("def sym1(): pass\ndef sym2(): pass\n")
        (tmp_path / "modul_b.py").write_text("def sym3(): pass\ndef sym4(): pass\n")
        called_tools = {"batch_extract_symbols{'source':'x','target':'modul_b.py'}": 1}
        ftype, evidence = classify_failure(
            task_node, called_tools, [], "lavet 2/3 moduler", agent)
        assert ftype == "incomplete"
        assert evidence.get("modules_planned") == 3
        assert evidence.get("modules_created") == 2
        assert "modul_c.py" in evidence.get("missing_modules", [])

    def test_incomplete_not_refactor(self, mock_agent, mock_task_node):
        from agent_autoresearch import classify_failure
        called_tools = {"read_issue{}": 1}
        ftype, _ = classify_failure(
            mock_task_node, called_tools, [], "tekst", mock_agent)
        assert ftype == "missing_tool"  # not incomplete — agent is issue_handler

    def test_incomplete_all_modules_done(self, tmp_path, monkeypatch):
        from agent_autoresearch import classify_failure
        agent = MagicMock()
        agent.active_template = "refactor"
        agent.tool_registry.active_tools = ["batch_extract_symbols", "verify_refactor"]
        task_node = MagicMock()
        task_node.name = "Ekstraher"
        monkeypatch.chdir(tmp_path)
        plan = tmp_path / "refactor_plan.md"
        plan.write_text(
            "## Module: modul_a.py\n**Symboler (2):** sym1, sym2\n",
            encoding="utf-8"
        )
        (tmp_path / "modul_a.py").write_text("def sym1(): pass\n")
        called_tools = {"batch_extract_symbols{}": 1}
        ftype, _ = classify_failure(
            task_node, called_tools, [], "alt færdigt", agent)
        # All modules exist → not incomplete, falls through to unknown
        assert ftype != "incomplete"


class TestCheckIssueFixApplied:
    def test_incomplete_fix_not_applied(self):
        """Real agent_tasks.py mangler dynamic budget — check returnerer False."""
        from agent_autoresearch import _check_issue_fix_applied, FAILURE_INCOMPLETE
        result = _check_issue_fix_applied(
            FAILURE_INCOMPLETE, {"modules_planned": 3}, "refactor", "Ekstraher")
        assert result is False

    def test_incomplete_fix_applied(self):
        """Mock agent_tasks.py med dynamic budget — check returnerer True."""
        from agent_autoresearch import _check_issue_fix_applied, FAILURE_INCOMPLETE
        from unittest.mock import mock_open, patch
        import os
        mock_source = (
            "def _get_max_iterations(agent, task_name):\n"
            "    if template == 'refactor' and task_lower == 'ekstraher':\n"
            "        from file_checks import _parse_refactor_plan_modules\n"
            "        pp = os.path.join(wd, 'refactor_plan.md')\n"
            "        if os.path.exists(pp):\n"
            "            mods = _parse_refactor_plan_modules(pp)\n"
            "            if mods:\n"
            "                return max(20, 2 + len(mods) * 2 + 5)\n"
            "    return 6\n"
        )
        original_exists = os.path.exists
        def _mock_exists(path):
            if "agent_tasks.py" in str(path):
                return True
            return original_exists(path)
        with patch("builtins.open", mock_open(read_data=mock_source)):
            with patch("os.path.exists", _mock_exists):
                result = _check_issue_fix_applied(
                    FAILURE_INCOMPLETE, {}, "refactor", "Ekstraher")
                assert result is True

    def test_incomplete_fix_other_failure(self):
        """Non-INCOMPLETE failure returnerer altid False."""
        from agent_autoresearch import _check_issue_fix_applied, FAILURE_MISSING_TOOL
        result = _check_issue_fix_applied(
            FAILURE_MISSING_TOOL, {}, "refactor", "Ekstraher")
        assert result is False


class TestFindDuplicateIssue:
    def test_exact_match_same_template(self):
        from agent_autoresearch import _find_duplicate_issue
        issues = [
            {"id": "CORE-001", "status": "open",
             "title": "Manglende vaerktoej i issue_handler/Luk Issue: update_issue_status",
             "description": "**Template:** issue_handler\n**Fase:** Luk Issue"},
        ]
        dup = _find_duplicate_issue(
            "missing_tool", "issue_handler", "Luk Issue",
            {"uncalled": ["update_issue_status"]}, issues)
        assert dup == "CORE-001"

    def test_no_match_different_phase(self):
        from agent_autoresearch import _find_duplicate_issue
        issues = [
            {"id": "CORE-001", "status": "open",
             "title": "Manglende vaerktoej i issue_handler/Afklar",
             "description": "**Template:** issue_handler\n**Fase:** Afklar"},
        ]
        dup = _find_duplicate_issue(
            "missing_tool", "issue_handler", "Fix",
            {"uncalled": ["update_issue_status"]}, issues)
        assert dup is None

    def test_resolved_issue_ignored(self):
        from agent_autoresearch import _find_duplicate_issue
        issues = [
            {"id": "CORE-001", "status": "resolved",
             "title": "Manglende vaerktoej i issue_handler/Luk Issue: update_issue_status",
             "description": "**Template:** issue_handler\n**Fase:** Luk Issue"},
        ]
        dup = _find_duplicate_issue(
            "missing_tool", "issue_handler", "Luk Issue",
            {"uncalled": ["update_issue_status"]}, issues)
        assert dup is None

    def test_empty_issues_list(self):
        from agent_autoresearch import _find_duplicate_issue
        dup = _find_duplicate_issue(
            "missing_tool", "issue_handler", "Luk Issue", {}, [])
        assert dup is None


class TestCreateIssue:
    def test_creates_issue(self, mock_agent):
        from agent_autoresearch import _create_issue
        with patch("agent_issues.create_issue") as mock_create:
            mock_create.return_value = {
                "success": True, "issue": {"id": "CORE-042"}, "existing": False}
            _create_issue(mock_agent, "missing_tool",
                          {"uncalled": ["edit_file"]},
                          "issue_handler", "Luk Issue",
                          "Analysis summary here")
            mock_create.assert_called_once()
            args = mock_create.call_args
            assert args[1]["title"] is not None
            assert args[1]["description"] is not None

    def test_handles_failure(self, mock_agent):
        from agent_autoresearch import _create_issue
        with patch("agent_issues.create_issue") as mock_create:
            mock_create.return_value = {"success": False, "error": "bang"}
            _create_issue(mock_agent, "missing_tool",
                          {"uncalled": ["x"]},
                          "issue_handler", "Luk Issue",
                          "analysis")
            mock_create.assert_called_once()


class TestTriggerIfNeeded:
    def test_skips_when_status_not_failed(self, mock_agent, mock_task_node):
        from agent_autoresearch import trigger_if_needed
        mock_task_node.status = "done"
        with patch("agent_autoresearch._rate_limit_ok") as mock_rate:
            trigger_if_needed(mock_agent, mock_task_node, {}, "ok", [])
            mock_rate.assert_not_called()

    def test_skips_on_rate_limit(self, mock_agent, mock_task_node):
        from agent_autoresearch import trigger_if_needed
        with patch("agent_autoresearch._rate_limit_ok", return_value=False), \
             patch("agent_autoresearch._find_duplicate_issue") as mock_dedup:
            trigger_if_needed(mock_agent, mock_task_node,
                              {"read_issue{}": 1}, "", [])
            mock_dedup.assert_not_called()

    def test_skips_on_duplicate(self, mock_agent, mock_task_node):
        from agent_autoresearch import trigger_if_needed
        mock_agent.autoresearch_enabled = True
        with patch("agent_autoresearch._rate_limit_ok", return_value=True), \
             patch("agent_issues._load_issues") as mock_load:
            mock_load.return_value = {
                "issues": [
                    {"id": "CORE-001", "status": "open",
                     "title": "Manglende vaerktoej i issue_handler/Luk Issue: update_issue_status",
                     "description": "**Template:** issue_handler\n**Fase:** Luk Issue"},
                ]
            }
            with patch("agent_autoresearch._create_issue") as mock_create:
                trigger_if_needed(mock_agent, mock_task_node,
                                  {"read_issue{}": 1}, "", [])
                mock_create.assert_not_called()

    def test_starts_research_on_novel_failure(self, mock_agent, mock_task_node):
        from agent_autoresearch import trigger_if_needed
        mock_agent.autoresearch_enabled = True
        with patch("agent_autoresearch._rate_limit_ok", return_value=True), \
             patch("agent_issues._load_issues") as mock_load, \
             patch("agent_issues.create_issue") as mock_create_issue:
            mock_load.return_value = {"issues": [], "meta": {"total": 0}}
            mock_create_issue.return_value = {"success": True, "issue": {"id": "CORE-999"}}
            result = trigger_if_needed(mock_agent, mock_task_node,
                                       {"read_issue{}": 1}, "", [])
            assert result == "CORE-999", f"Expected issue_id, got {result}"

    def test_trigger_incomplete_creates_core_issue(self, tmp_path, monkeypatch):
        """Integration test: refactor Ekstraher med 2/3 moduler → CORE-issue."""
        from agent_autoresearch import trigger_if_needed
        agent = MagicMock()
        agent.active_template = "refactor"
        agent.autoresearch_enabled = True
        agent._session_id = "test-incomplete-001"
        agent._tool_log = []
        agent.tool_registry.active_tools = ["batch_extract_symbols", "verify_refactor"]
        agent._autoresearch_depth = 0
        task_node = MagicMock()
        task_node.name = "Ekstraher"
        task_node.status = "failed"
        monkeypatch.chdir(tmp_path)
        plan = tmp_path / "refactor_plan.md"
        plan.write_text(
            "## Module: modul_a.py\n**Symboler (2):** sym1, sym2\n\n"
            "## Module: modul_b.py\n**Symboler (2):** sym3, sym4\n\n"
            "## Module: modul_c.py\n**Symboler (1):** sym5\n",
            encoding="utf-8"
        )
        (tmp_path / "modul_a.py").write_text("def sym1(): pass\n")
        (tmp_path / "modul_b.py").write_text("def sym3(): pass\n")
        called_tools = {"batch_extract_symbols{}": 2}
        with patch("agent_autoresearch._rate_limit_ok", return_value=True), \
             patch("agent_issues._load_issues") as mock_load, \
             patch("agent_issues.create_issue") as mock_create:
            mock_load.return_value = {"issues": [], "meta": {"total": 0}}
            mock_create.return_value = {"success": True, "issue": {"id": "CORE-050"}}
            result = trigger_if_needed(agent, task_node, called_tools, "2/3 moduler færdige", [])
            assert result == "CORE-050"
            mock_create.assert_called_once()
            call_args = mock_create.call_args[1]
            assert "incomplete" in call_args.get("title", "").lower() or \
                   "ufuldstændig" in call_args.get("title", "").lower()
            assert "modul_c.py" in call_args.get("description", "")
            assert call_args["type"] == "self"

    def test_trigger_auto_resolves_when_fix_applied(self, tmp_path, monkeypatch):
        """When duplicate findes og fix_applied=True → auto-resolve eksisterende issue."""
        from agent_autoresearch import trigger_if_needed
        import os
        agent = MagicMock()
        agent.active_template = "refactor"
        agent.autoresearch_enabled = True
        agent._session_id = "test-autoresolve-001"
        agent._tool_log = []
        agent._autoresearch_depth = 0
        agent.tool_registry.active_tools = ["batch_extract_symbols", "verify_refactor"]
        task_node = MagicMock()
        task_node.name = "Ekstraher"
        task_node.status = "failed"
        monkeypatch.chdir(tmp_path)
        plan = tmp_path / "refactor_plan.md"
        plan.write_text(
            "## Module: modul_a.py\n**Symboler (2):** sym1, sym2\n\n"
            "## Module: modul_b.py\n**Symboler (2):** sym3, sym4\n\n"
            "## Module: modul_c.py\n**Symboler (1):** sym5\n",
            encoding="utf-8"
        )
        (tmp_path / "modul_a.py").write_text("def sym1(): pass\n")
        (tmp_path / "modul_b.py").write_text("def sym3(): pass\n")
        called_tools = {"batch_extract_symbols{}": 2}
        with patch("agent_autoresearch._rate_limit_ok", return_value=True), \
             patch("agent_autoresearch._check_issue_fix_applied", return_value=True), \
             patch("agent_issues._load_issues") as mock_load, \
             patch("agent_issues.update_issue_status") as mock_update:
            mock_load.return_value = {
                "issues": [
                    {"id": "CORE-101", "status": "open",
                     "title": "Ufuldstændig ekstrahering i refactor/Ekstraher",
                     "description": "**Template:** refactor\n**Fase:** Ekstraher"},
                ]
            }
            result = trigger_if_needed(
                agent, task_node, called_tools, "2/3 moduler færdige", [])
            assert result is None  # No new issue created
            mock_update.assert_called_once()
            call_args = mock_update.call_args
            assert call_args[0][1] == "CORE-101"
            assert call_args[0][2] == "resolved"
            assert "implementeret" in call_args[0][3]

    def test_trigger_skips_when_fix_not_applied(self, tmp_path, monkeypatch):
        """When duplicate findes og fix_applied=False → skip (ingen ny issue, ingen resolve)."""
        from agent_autoresearch import trigger_if_needed
        agent = MagicMock()
        agent.active_template = "refactor"
        agent.autoresearch_enabled = True
        agent._session_id = "test-skip-001"
        agent._tool_log = []
        agent._autoresearch_depth = 0
        agent.tool_registry.active_tools = ["batch_extract_symbols", "verify_refactor"]
        task_node = MagicMock()
        task_node.name = "Ekstraher"
        task_node.status = "failed"
        monkeypatch.chdir(tmp_path)
        plan = tmp_path / "refactor_plan.md"
        plan.write_text(
            "## Module: modul_a.py\n**Symboler (2):** sym1, sym2\n\n"
            "## Module: modul_b.py\n**Symboler (2):** sym3, sym4\n\n"
            "## Module: modul_c.py\n**Symboler (1):** sym5\n",
            encoding="utf-8"
        )
        (tmp_path / "modul_a.py").write_text("def sym1(): pass\n")
        (tmp_path / "modul_b.py").write_text("def sym3(): pass\n")
        called_tools = {"batch_extract_symbols{}": 2}
        with patch("agent_autoresearch._rate_limit_ok", return_value=True), \
             patch("agent_autoresearch._check_issue_fix_applied", return_value=False), \
             patch("agent_issues._load_issues") as mock_load, \
             patch("agent_issues.update_issue_status") as mock_update:
            mock_load.return_value = {
                "issues": [
                    {"id": "CORE-101", "status": "open",
                     "title": "Ufuldstændig ekstrahering i refactor/Ekstraher",
                     "description": "**Template:** refactor\n**Fase:** Ekstraher"},
                ]
            }
            result = trigger_if_needed(
                agent, task_node, called_tools, "2/3 moduler færdige", [])
            assert result is None  # Duplicate → skippet
            mock_update.assert_not_called()  # Ingen resolve


class TestEventQueue:
    def test_emit_and_read_events(self):
        from agent_autoresearch import _emit_event, get_events, _LOG_DIR
        import os, json, time
        rid = "test-event-queue-001"
        # Clean up
        path = os.path.join(_LOG_DIR, rid)
        if os.path.exists(path):
            import shutil
            shutil.rmtree(path)

        _emit_event(rid, "test_event", {"msg": "hello"})
        time.sleep(0.05)

        events = get_events(rid)
        assert len(events) == 1
        assert events[0]["type"] == "test_event"
        assert events[0]["msg"] == "hello"

        # Test since filter
        events_since = get_events(rid, since=time.time() + 10)
        assert len(events_since) == 0

    def test_get_active_sessions(self):
        from agent_autoresearch import get_active_sessions, _save_state, _LOG_DIR
        import os
        rid = "test-active-session-001"
        path = os.path.join(_LOG_DIR, rid)
        if os.path.exists(path):
            import shutil
            shutil.rmtree(path)

        _save_state(rid, {
            "research_id": rid,
            "status": "running",
            "template": "test",
        })
        sessions = get_active_sessions()
        assert any(s.get("research_id") == rid for s in sessions)
        assert all(s.get("status") in ("running", "paused") for s in sessions)


class TestRateLimit:
    def test_rate_limit_ok_first_call(self):
        from agent_autoresearch import _rate_limit_ok, _last_analysis
        _last_analysis.clear()
        assert _rate_limit_ok("session-1") is True

    def test_rate_limit_blocks_within_window(self):
        from agent_autoresearch import _rate_limit_ok, _last_analysis
        _last_analysis.clear()
        _rate_limit_ok("session-1")
        assert _rate_limit_ok("session-1") is False

    def test_rate_limit_allows_different_sessions(self):
        from agent_autoresearch import _rate_limit_ok, _last_analysis
        _last_analysis.clear()
        _rate_limit_ok("session-1")
        assert _rate_limit_ok("session-2") is True
