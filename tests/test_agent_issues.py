"""Test agent_issues.py — issue CRUD, oversize detection."""
import json
import os
from unittest.mock import MagicMock, patch


class TestNextRefacId:
    def test_first_id_when_none_exist(self):
        from agent_issues import _next_refac_id
        data = {"issues": []}
        assert _next_refac_id(data) == "REFAC-001"

    def test_increments_from_existing(self):
        from agent_issues import _next_refac_id
        data = {"issues": [
            {"id": "REFAC-001"}, {"id": "REFAC-005"}, {"id": "SEC-001"}
        ]}
        assert _next_refac_id(data) == "REFAC-006"

    def test_ignores_non_refac_ids(self):
        from agent_issues import _next_refac_id
        data = {"issues": [
            {"id": "BUG-001"}, {"id": "SEC-001"}
        ]}
        assert _next_refac_id(data) == "REFAC-001"


class TestDetectOversizeFile:
    def test_under_limit_returns_none(self):
        from agent_issues import detect_oversize_file
        agent = MagicMock()
        result = detect_oversize_file(agent, "small.py", "x = 1\n")
        assert result is None

    def test_over_limit_sets_pending_refactor(self):
        from agent_issues import detect_oversize_file, OVERSIZE_LINE_LIMIT
        agent = MagicMock()
        content = "\n".join(f"line {i}" for i in range(OVERSIZE_LINE_LIMIT + 10))
        result = detect_oversize_file(agent, "big.py", content)
        assert result is not None
        assert result["file"] == "big.py"
        assert result["lines"] >= OVERSIZE_LINE_LIMIT
        assert agent._pending_refactor is not None

    def test_exact_limit_returns_none(self):
        from agent_issues import detect_oversize_file, OVERSIZE_LINE_LIMIT
        agent = MagicMock()
        content = "\n".join(f"line {i}" for i in range(OVERSIZE_LINE_LIMIT - 1))
        result = detect_oversize_file(agent, "ok.py", content)
        assert result is None


class TestReadIssue:
    def test_read_existing_issue(self, tmp_path):
        from agent_issues import read_issue, _get_issues_path, _save_issues
        data = {"meta": {"total": 1}, "issues": [
            {"id": "BUG-001", "title": "Test bug", "status": "open"}
        ]}
        with patch("agent_issues._get_issues_path", return_value=str(tmp_path / "issues.json")):
            _save_issues(data)
            result = read_issue("BUG-001")
            assert result["success"] is True
            assert result["issue"]["title"] == "Test bug"

    def test_read_nonexistent_issue(self, tmp_path):
        from agent_issues import read_issue, _get_issues_path, _save_issues
        data = {"meta": {"total": 0}, "issues": []}
        with patch("agent_issues._get_issues_path", return_value=str(tmp_path / "issues.json")):
            _save_issues(data)
            result = read_issue("BUG-999")
            assert result["success"] is False


class TestUpdateIssueStatus:
    def test_update_status(self, tmp_path):
        from agent_issues import update_issue_status, _get_issues_path, _save_issues
        data = {"meta": {"total": 1}, "issues": [
            {"id": "BUG-001", "title": "Test bug", "status": "open"}
        ]}
        with patch("agent_issues._get_issues_path", return_value=str(tmp_path / "issues.json")):
            _save_issues(data)
            agent = MagicMock()
            result = update_issue_status(agent, "BUG-001", "resolved", "fix: tested")
            assert result["success"] is True
            assert result["status"] == "resolved"

    def test_resolve_referenced_issues(self, tmp_path):
        from agent_issues import update_issue_status, _get_issues_path, _save_issues
        data = {"meta": {"total": 2}, "issues": [
            {"id": "SEC-003", "title": "Path traversal in api_server.py", "description": "Related to BUG-005", "status": "open"},
            {"id": "BUG-005", "title": "Minor bug", "description": "Some issue", "status": "open"},
        ]}
        with patch("agent_issues._get_issues_path", return_value=str(tmp_path / "issues.json")):
            _save_issues(data)
            agent = MagicMock()
            result = update_issue_status(agent, "SEC-003", "resolved", "Fix applied, closes BUG-005")
            assert result["success"] is True
            from agent_issues import _load_issues
            loaded = _load_issues()
            bug005 = [i for i in loaded["issues"] if i["id"] == "BUG-005"][0]
            assert bug005["status"] == "resolved"
            assert "SEC-003" in bug005["resolution_note"]


class TestCreateRefactorIssue:
    def test_creates_new_issue(self, tmp_path):
        from agent_issues import create_refactor_issue, _get_issues_path, _save_issues
        data = {"meta": {"total": 0}, "issues": []}
        with patch("agent_issues._get_issues_path", return_value=str(tmp_path / "issues.json")):
            _save_issues(data)
            agent = MagicMock()
            result = create_refactor_issue(agent, "src/big.py", 1500)
            assert result["success"] is True
            assert result["existing"] is False
            assert result["issue"]["id"].startswith("REFAC-")

    def test_returns_existing_if_duplicate(self, tmp_path):
        from agent_issues import create_refactor_issue, _get_issues_path, _save_issues
        data = {"meta": {"total": 1}, "issues": [
            {"id": "REFAC-001", "type": "refactor", "location": "src/big.py"}
        ]}
        with patch("agent_issues._get_issues_path", return_value=str(tmp_path / "issues.json")):
            _save_issues(data)
            agent = MagicMock()
            result = create_refactor_issue(agent, "src/big.py", 1500)
            assert result["success"] is True
            assert result["existing"] is True


class TestRunPytest:
    def test_run_pytest_success(self):
        from agent_issues import run_pytest
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "OK"
            mock_run.return_value.stderr = ""
            result = run_pytest()
            assert result["success"] is True

    def test_run_pytest_failure(self):
        from agent_issues import run_pytest
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = "FAIL"
            mock_run.return_value.stderr = "error"
            result = run_pytest()
            assert result["success"] is False

    def test_run_pytest_in_workdir(self):
        from agent_issues import run_pytest
        with patch("subprocess.run") as mock_run, \
             patch.dict("os.environ", {"AGENT_WORKDIR": "C:/Dev/StarBrowser"}, clear=True):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "OK"
            mock_run.return_value.stderr = ""
            result = run_pytest()
            assert result["success"] is True
            _, kwargs = mock_run.call_args
            assert kwargs["cwd"] == "C:/Dev/StarBrowser"

    def test_run_pytest_falls_back_to_cwd(self):
        from agent_issues import run_pytest
        with patch("subprocess.run") as mock_run, \
             patch("os.getcwd", return_value="C:/Dev/Agenten"), \
             patch.dict("os.environ", {"AGENT_WORKDIR": ""}, clear=True):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "OK"
            mock_run.return_value.stderr = ""
            result = run_pytest()
            assert result["success"] is True
            _, kwargs = mock_run.call_args
            assert kwargs["cwd"] == "C:/Dev/Agenten"


class TestCreateIssue:
    def test_create_new_issue(self, tmp_path):
        from agent_issues import create_issue, _get_issues_path, _save_issues
        data = {"meta": {"total": 0}, "issues": []}
        with patch("agent_issues._get_issues_path", return_value=str(tmp_path / "issues.json")):
            _save_issues(data)
            agent = MagicMock()
            result = create_issue(agent, "Test bug found", "bug", "high",
                                  "A test description", "file.py:10", "High impact", "Fix it")
            assert result["success"] is True
            assert result["existing"] is False
            assert result["issue"]["title"] == "Test bug found"
            assert result["issue"]["type"] == "bug"
            assert result["issue"]["severity"] == "high"
            assert result["issue"]["status"] == "open"

    def test_create_issue_duplicate_title(self, tmp_path):
        from agent_issues import create_issue, _get_issues_path, _save_issues
        data = {"meta": {"total": 1}, "issues": [
            {"id": "BUG-001", "title": "Duplicate title", "type": "bug"}
        ]}
        with patch("agent_issues._get_issues_path", return_value=str(tmp_path / "issues.json")):
            _save_issues(data)
            agent = MagicMock()
            result = create_issue(agent, "Duplicate title", "bug", "medium", "desc")
            assert result["success"] is True
            assert result["existing"] is True

    def test_create_issue_generates_correct_id(self, tmp_path):
        from agent_issues import create_issue, _get_issues_path, _save_issues
        data = {"meta": {"total": 5}, "issues": [
            {"id": "BUG-001"}, {"id": "BUG-003"}, {"id": "SEC-001"},
            {"id": "ARC-001"}, {"id": "TST-001"}
        ]}
        with patch("agent_issues._get_issues_path", return_value=str(tmp_path / "issues.json")):
            _save_issues(data)
            agent = MagicMock()
            r1 = create_issue(agent, "Bug A", "bug", "low", "")
            assert r1["issue"]["id"] == "BUG-004"
            r2 = create_issue(agent, "Sec A", "security", "high", "")
            assert r2["issue"]["id"] == "SEC-002"
            r3 = create_issue(agent, "Arc A", "architecture", "medium", "")
            assert r3["issue"]["id"] == "ARC-002"
            r4 = create_issue(agent, "Tst A", "testing", "low", "")
            assert r4["issue"]["id"] == "TST-002"

    def test_create_issue_first_id(self, tmp_path):
        from agent_issues import create_issue, _get_issues_path, _save_issues
        data = {"meta": {"total": 0}, "issues": []}
        with patch("agent_issues._get_issues_path", return_value=str(tmp_path / "issues.json")):
            _save_issues(data)
            agent = MagicMock()
            result = create_issue(agent, "First bug", "bug", "low", "")
            assert result["issue"]["id"] == "BUG-001"
            result = create_issue(agent, "First perf", "performance", "low", "")
            assert result["issue"]["id"] == "PRF-001"
            result = create_issue(agent, "First mnt", "maintainability", "low", "")
            assert result["issue"]["id"] == "MNT-001"

    def test_create_issue_duplicate_location(self, tmp_path):
        from agent_issues import create_issue, _get_issues_path, _save_issues
        data = {"meta": {"total": 1}, "issues": [
            {"id": "SEC-003", "title": "Path traversal in upload", "location": "api_server.py:189", "type": "security"}
        ]}
        with patch("agent_issues._get_issues_path", return_value=str(tmp_path / "issues.json")):
            _save_issues(data)
            agent = MagicMock()
            result = create_issue(agent, "File upload path traversal", "security", "high",
                                  "desc", "api_server.py:253")
            assert result["success"] is True
            assert result["existing"] is True

    def test_create_issue_duplicate_keywords(self, tmp_path):
        from agent_issues import create_issue, _get_issues_path, _save_issues
        data = {"meta": {"total": 1}, "issues": [
            {"id": "SEC-008", "title": "Path Traversal Vulnerability via sequences in file upload endpoints", "location": "", "type": "security"}
        ]}
        with patch("agent_issues._get_issues_path", return_value=str(tmp_path / "issues.json")):
            _save_issues(data)
            agent = MagicMock()
            result = create_issue(agent, "Path traversal vulnerability in file upload serving endpoints",
                                  "security", "high", "desc")
            assert result["success"] is True
            assert result["existing"] is True
