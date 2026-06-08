"""Tests for CoreAnalytics in core_analytics.py."""

import json
import os
import tempfile

from core_analytics import CoreAnalytics, TOOL_HANDLER_MAP


# ============ Tool outcome recording ============


def test_record_tool_outcome_success():
    ca = CoreAnalytics()
    ca.record_tool_outcome("edit_file", True)
    entry = ca.data["tools"]["edit_file"]
    assert entry["calls"] == 1
    assert entry["failures"] == 0
    assert entry["handler"] == "git_ops.py"


def test_record_tool_outcome_failure():
    ca = CoreAnalytics()
    ca.record_tool_outcome("edit_file", False, error="old_string not found")
    entry = ca.data["tools"]["edit_file"]
    assert entry["calls"] == 1
    assert entry["failures"] == 1
    assert entry["last_error"] == "old_string not found"


def test_record_tool_outcome_accumulates():
    ca = CoreAnalytics()
    ca.record_tool_outcome("edit_file", True)
    ca.record_tool_outcome("edit_file", False, error="match ambiguous")
    ca.record_tool_outcome("edit_file", True)
    entry = ca.data["tools"]["edit_file"]
    assert entry["calls"] == 3
    assert entry["failures"] == 1


def test_record_tool_outcome_unknown_tool():
    """Unknown tools should still be recorded with handler 'unknown.py'."""
    ca = CoreAnalytics()
    ca.record_tool_outcome("mystery_tool", False, error="broken")
    entry = ca.data["tools"]["mystery_tool"]
    assert entry["handler"] == "unknown.py"
    assert entry["failures"] == 1


def test_record_tool_outcome_error_dedup():
    """Same error string should increment its counter."""
    ca = CoreAnalytics()
    ca.record_tool_outcome("locate", False, error="not found")
    ca.record_tool_outcome("locate", False, error="not found")
    ca.record_tool_outcome("locate", False, error="timeout")
    entry = ca.data["tools"]["locate"]
    assert entry["errors"]["not found"] == 2
    assert entry["errors"]["timeout"] == 1


def test_record_tool_outcome_custom_handler():
    """Custom handler_file should override the default map."""
    ca = CoreAnalytics()
    ca.record_tool_outcome("edit_file", False, error="fail", handler_file="custom.py")
    assert ca.data["tools"]["edit_file"]["handler"] == "custom.py"


# ============ Test outcome recording ============


def test_record_test_outcome_passed():
    ca = CoreAnalytics()
    ca.record_test_outcome("tests/test_api.py", True, summary="10 passed")
    entry = ca.data["tests"]["tests/test_api.py"]
    assert entry["runs"] == 1
    assert entry["passes"] == 1
    assert entry["failures"] == 0


def test_record_test_outcome_failed():
    ca = CoreAnalytics()
    ca.record_test_outcome("tests/test_api.py", False, summary="2 failed")
    entry = ca.data["tests"]["tests/test_api.py"]
    assert entry["runs"] == 1
    assert entry["passes"] == 0
    assert entry["failures"] == 1
    assert "2 failed" in entry["last_failure"]


def test_record_test_outcome_accumulates():
    ca = CoreAnalytics()
    ca.record_test_outcome("tests/test_wta.py", True)
    ca.record_test_outcome("tests/test_wta.py", False)
    ca.record_test_outcome("tests/test_wta.py", True)
    entry = ca.data["tests"]["tests/test_wta.py"]
    assert entry["runs"] == 3
    assert entry["passes"] == 2
    assert entry["failures"] == 1


# ============ Edit tracking ============


def test_record_edit():
    ca = CoreAnalytics()
    ca.record_edit("git_ops.py")
    ca.record_edit("git_ops.py")
    assert ca.data["edits"]["git_ops.py"]["count"] == 2


# ============ Session tracking ============


def test_record_session_success():
    ca = CoreAnalytics()
    ca.record_session(True)
    assert ca.data["sessions"]["total"] == 1
    assert ca.data["sessions"]["failed"] == 0


def test_record_session_failure():
    ca = CoreAnalytics()
    ca.record_session(False, error="HTTP 400")
    assert ca.data["sessions"]["total"] == 1
    assert ca.data["sessions"]["failed"] == 1
    assert "HTTP 400" in ca.data["sessions"]["recent_errors"]


# ============ Hotspots ============


def test_get_hotspots_empty():
    ca = CoreAnalytics()
    assert ca.get_hotspots() == []


def test_get_hotspots_orders_by_failures():
    ca = CoreAnalytics()
    ca.record_tool_outcome("edit_file", False, error="err1")
    ca.record_tool_outcome("edit_file", False, error="err2")
    ca.record_tool_outcome("run_tests", False, error="err3")
    ca.record_tool_outcome("locate", False, error="err4")  # 1 failure only — below default threshold

    spots = ca.get_hotspots(min_failures=2)
    names = [s["file"] for s in spots]
    assert "git_ops.py" in names
    assert "agent_files.py" not in names


def test_get_hotspots_tool_failures_sum():
    """Multiple tools in the same handler should sum their failures."""
    ca = CoreAnalytics()
    ca.record_tool_outcome("edit_file", False, error="e1")
    ca.record_tool_outcome("edit_file", False, error="e2")
    ca.record_tool_outcome("write_file", False, error="e3")
    ca.record_tool_outcome("write_file", False, error="e4")
    spots = ca.get_hotspots(min_failures=1)
    git_ops = [s for s in spots if s["file"] == "git_ops.py"]
    assert len(git_ops) == 1
    assert git_ops[0]["tool_failures"] == 2
    agent_files = [s for s in spots if s["file"] == "agent_files.py"]
    assert len(agent_files) == 1
    assert agent_files[0]["tool_failures"] == 2


# ============ Summary ============


def test_get_summary_with_hotspots():
    ca = CoreAnalytics()
    ca.record_tool_outcome("edit_file", False, error="err1")
    ca.record_tool_outcome("edit_file", False, error="err2")
    summary = ca.get_summary(min_failures=2)
    assert "git_ops.py" in summary
    assert "2 værktøjsfejl" in summary


def test_get_summary_empty():
    ca = CoreAnalytics()
    summary = ca.get_summary()
    assert summary == ""


def test_get_summary_includes_tests():
    ca = CoreAnalytics()
    ca.record_test_outcome("tests/test_api.py", False, summary="AssertionError")
    summary = ca.get_summary()
    assert "test_api.py" in summary
    assert "1/1" in summary or "1 fejlede" in summary


def test_get_summary_includes_sessions():
    ca = CoreAnalytics()
    ca.record_session(True)
    ca.record_session(False, error="timeout")
    summary = ca.get_summary()
    assert "2 i alt" in summary
    assert "1 fejlede" in summary or "50" in summary


# ============ Persistence ============


def test_persist_roundtrip():
    ca = CoreAnalytics()
    ca.record_tool_outcome("edit_file", False, error="not found")
    ca.record_test_outcome("tests/test_wta.py", True, summary="5 passed")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        tmp_path = f.name
        ca.path = tmp_path
        ca.save()

    ca2 = CoreAnalytics(path=tmp_path)
    ca2.load()
    assert ca2.data["tools"]["edit_file"]["failures"] == 1
    assert ca2.data["tests"]["tests/test_wta.py"]["passes"] == 1
    os.unlink(tmp_path)


# ============ TOOL_HANDLER_MAP ============


def test_tool_handler_map_contains_key_tools():
    for tool in ("edit_file", "write_file", "run_tests", "locate", "read_issue"):
        assert tool in TOOL_HANDLER_MAP, f"{tool} missing from TOOL_HANDLER_MAP"


def test_tool_handler_map_no_empty_handlers():
    for tool, handler in TOOL_HANDLER_MAP.items():
        assert handler, f"{tool} has empty handler"
        assert handler.endswith(".py"), f"{tool} handler {handler} does not end with .py"
