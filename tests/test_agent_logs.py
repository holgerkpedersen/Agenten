"""Tests for analyze_own_logs in agent_logs.py."""

import json
import os
import tempfile

from agent_logs import analyze_own_logs, _count_by_level, _tool_call_stats, _task_summary, _error_patterns, _search_logs, _list_session_files, _SESSIONS_DIR


# ============ Fixtures ============


def _make_session(overrides: dict = None) -> dict:
    session = {
        "id": "test-001",
        "name": "Test Session",
        "created": "2026-01-01T00:00:00",
        "template": "bugfix",
        "active_template": "bugfix",
        "execute_model": "deepseek-v4-pro",
        "original_prompt": "Fix the bug in api_server.py",
        "agent_log": [],
        "execution_log": [],
    }
    if overrides:
        session.update(overrides)
    return session


def _session_file_content(session: dict) -> str:
    return json.dumps(session, ensure_ascii=False, indent=2)


# ============ _count_by_level ============


def test_count_by_level_empty():
    assert _count_by_level([]) == {}


def test_count_by_level_counts():
    entries = [
        {"level": "INFO", "message": "a"},
        {"level": "ERROR", "message": "b"},
        {"level": "INFO", "message": "c"},
    ]
    counts = _count_by_level(entries)
    assert counts["INFO"] == 2
    assert counts["ERROR"] == 1


# ============ _tool_call_stats ============


def test_tool_call_stats_empty():
    assert _tool_call_stats([]) == {"total_calls": 0, "failures": 0, "tools": {}}


def test_tool_call_stats_extracts():
    entries = [
        {"level": "TOOL", "message": "Kaldte v\u00e6rkt\u00f8j: edit_file", "detail": "success"},
        {"level": "TOOL", "message": "Kaldte v\u00e6rkt\u00f8j: run_tests", "detail": "failed"},
        {"level": "TOOL", "message": "Kaldte v\u00e6rkt\u00f8j: edit_file", "detail": "success"},
    ]
    stats = _tool_call_stats(entries)
    assert stats["total_calls"] == 3
    assert stats["tools"]["edit_file"] == 2
    assert stats["tools"]["run_tests"] == 1


# ============ _task_summary ============


def test_task_summary_empty():
    assert _task_summary({}) == {"total": 0, "failed": 0}


def test_task_summary_counts():
    session = _make_session({
        "execution_log": [
            {"name": "Analyse", "status": "done"},
            {"name": "Fix", "status": "failed"},
            {"name": "Test", "status": "done"},
        ]
    })
    summary = _task_summary(session)
    assert summary["total"] == 3
    assert summary["failed"] == 1


# ============ _error_patterns ============


def test_error_patterns_empty():
    assert _error_patterns([]) == []


def test_error_patterns_groups():
    entries = [
        {"level": "ERROR", "message": "HTTP 400", "detail": "bad request"},
        {"level": "ERROR", "message": "HTTP 400", "detail": "bad request"},
        {"level": "WARNING", "message": "Timeout", "detail": ""},
    ]
    pats = _error_patterns(entries)
    assert len(pats) >= 1
    assert "HTTP 400" in pats[0]


# ============ _search_logs ============


def test_search_logs_empty():
    assert _search_logs([], "foo") == []


def test_search_logs_matches():
    entries = [
        {"message": "Starter nedbrydning", "detail": "bugfix"},
        {"message": "Kaldte v\u00e6rkt\u00f8j", "detail": "edit_file"},
        {"message": "Fejl", "detail": "noget gik galt"},
    ]
    matches = _search_logs(entries, "edit")
    assert len(matches) == 1
    assert matches[0]["detail"] == "edit_file"


def test_search_logs_regex():
    entries = [
        {"message": "Error 500", "detail": "server"},
        {"message": "Error 404", "detail": "not found"},
        {"message": "OK", "detail": "all good"},
    ]
    matches = _search_logs(entries, r"Error \d{3}")
    assert len(matches) == 2


# ============ analyze_own_logs integration ============


def test_analyze_no_sessions():
    """When no sessions dir exists, should return error."""
    # Temporarily patch the sessions dir
    import agent_logs
    original = agent_logs._SESSIONS_DIR
    agent_logs._SESSIONS_DIR = os.path.join(tempfile.gettempdir(), "_agent_test_nosessions_" + str(os.getpid()))
    try:
        result = analyze_own_logs()
        assert result["success"] is False
        assert "Ingen session" in result.get("error", "")
    finally:
        agent_logs._SESSIONS_DIR = original


def test_analyze_recent_sessions(tmp_path):
    """With a few session files, should return summaries."""
    import agent_logs
    original = agent_logs._SESSIONS_DIR
    agent_logs._SESSIONS_DIR = str(tmp_path)

    try:
        # Create 2 session files
        s1 = _make_session({"id": "sess-001", "name": "First"})
        s2 = _make_session({
            "id": "sess-002", "name": "Second",
            "agent_log": [{"level": "ERROR", "message": "HTTP 400", "detail": "crash"}],
        })
        with open(os.path.join(tmp_path, "sess-001.json"), "w", encoding="utf-8") as f:
            f.write(_session_file_content(s1))
        with open(os.path.join(tmp_path, "sess-002.json"), "w", encoding="utf-8") as f:
            f.write(_session_file_content(s2))

        result = analyze_own_logs(max_sessions=5)
        assert result["success"] is True
        assert len(result["sessions"]) == 2
        # Both sessions should appear
        ids = {s["id"] for s in result["sessions"]}
        assert ids == {"sess-001", "sess-002"}
    finally:
        agent_logs._SESSIONS_DIR = original


def test_analyze_specific_session(tmp_path):
    """With session_id, should return detailed analysis."""
    import agent_logs
    original = agent_logs._SESSIONS_DIR
    agent_logs._SESSIONS_DIR = str(tmp_path)

    try:
        session = _make_session({
            "id": "sess-003",
            "name": "Deep Dive",
            "agent_log": [
                {"level": "INFO", "message": "Starter", "detail": "begin"},
                {"level": "ERROR", "message": "HTTP 400", "detail": "bad request"},
                {"level": "TOOL", "message": "Kaldte v\u00e6rkt\u00f8j: edit_file", "detail": "success"},
                {"level": "TOOL", "message": "Kaldte v\u00e6rkt\u00f8j: run_tests", "detail": "failed"},
                {"level": "WARNING", "message": "Timeout", "detail": "30s"},
            ],
            "execution_log": [
                {"name": "Analyse", "status": "done"},
                {"name": "Fix", "status": "failed"},
            ],
        })
        fp = os.path.join(tmp_path, "sess-003.json")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(_session_file_content(session))

        result = analyze_own_logs(session_id="sess-003")
        assert result["success"] is True
        assert "sess-003" in result["summary"]
        assert "ERROR" in result["summary"]
        assert "Tasks: 2 total, 1 fejlede" in result["summary"]
        assert "bad request" in result["summary"]
        assert result["session"]["total_tasks"] == 2
        assert result["session"]["failed_tasks"] == 1
    finally:
        agent_logs._SESSIONS_DIR = original


def test_analyze_with_pattern(tmp_path):
    """Pattern filtering should return matching log entries."""
    import agent_logs
    original = agent_logs._SESSIONS_DIR
    agent_logs._SESSIONS_DIR = str(tmp_path)

    try:
        session = _make_session({
            "id": "sess-004",
            "agent_log": [
                {"level": "ERROR", "message": "HTTP 400", "detail": "invalid input"},
                {"level": "INFO", "message": "Starter", "detail": "normal"},
            ],
        })
        fp = os.path.join(tmp_path, "sess-004.json")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(_session_file_content(session))

        result = analyze_own_logs(session_id="sess-004", pattern="HTTP")
        assert result["success"] is True
        assert result["match_count"] == 1
        assert len(result["matching_logs"]) == 1
        assert result["matching_logs"][0]["detail"] == "invalid input"
    finally:
        agent_logs._SESSIONS_DIR = original


def test_analyze_missing_session(tmp_path):
    """Unknown session_id should return error."""
    import agent_logs
    original = agent_logs._SESSIONS_DIR
    agent_logs._SESSIONS_DIR = str(tmp_path)

    try:
        # Need at least one session file so we get past the "no files" check
        s = _make_session({"id": "real-001"})
        with open(os.path.join(tmp_path, "real-001.json"), "w", encoding="utf-8") as f:
            f.write(_session_file_content(s))

        result = analyze_own_logs(session_id="nonexistent")
        assert result["success"] is False
        assert "ikke fundet" in result["error"]
    finally:
        agent_logs._SESSIONS_DIR = original


def test_analyze_invalid_json(tmp_path):
    """Corrupted session file should be skipped gracefully."""
    import agent_logs
    original = agent_logs._SESSIONS_DIR
    agent_logs._SESSIONS_DIR = str(tmp_path)

    try:
        with open(os.path.join(tmp_path, "bad.json"), "w", encoding="utf-8") as f:
            f.write("{invalid json}")
        # Should not crash
        result = analyze_own_logs(max_sessions=5)
        assert result["success"] is True
        assert len(result["sessions"]) == 0
    finally:
        agent_logs._SESSIONS_DIR = original
