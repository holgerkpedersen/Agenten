"""Test api_server.py — Flask API endpoints."""
import pytest
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from api_server import app as flask_app


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    import api_server
    api_server.current_session_id = None
    with flask_app.test_client() as c:
        yield c


class TestAPILang:
    def test_get_lang_da(self, client):
        resp = client.get("/api/lang/da")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "title" in data
        assert data["title"] == "Agenten"
        assert "decompose" in data

    def test_get_lang_en(self, client):
        resp = client.get("/api/lang/en")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "Decompose" in data["decompose"]

    def test_get_lang_es(self, client):
        resp = client.get("/api/lang/es")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "Descomponer" in data["decompose"]

    def test_get_lang_zh(self, client):
        resp = client.get("/api/lang/zh")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "分解" in data["decompose"]

    def test_get_lang_unknown_fallback(self, client):
        resp = client.get("/api/lang/xx")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "title" in data


class TestAPISessions:
    def test_list_sessions(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, (list, dict))
        if isinstance(data, dict):
            assert "sessions" in data or "success" in data
        else:
            assert isinstance(data, list)

    def test_create_session(self, client):
        resp = client.post("/api/sessions/create", json={"name": "pytest session"})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data.get("session_id") or data.get("session", {}).get("id")

    def test_create_session_no_name(self, client):
        resp = client.post("/api/sessions/create", json={})
        assert resp.status_code == 200

    def test_get_session(self, client):
        create_resp = client.post("/api/sessions/create", json={"name": "Get Test"})
        data = json.loads(create_resp.data)
        sid = data.get("session_id") or data.get("session", {}).get("id")
        resp = client.get(f"/api/sessions/load/{sid}")
        assert resp.status_code == 200
        loaded = json.loads(resp.data)
        assert loaded.get("session", {}).get("name") == "Get Test"

    def test_get_nonexistent_session(self, client):
        resp = client.get("/api/sessions/load/doesnotexist123")
        assert resp.status_code == 404

    def test_update_session(self, client):
        create_resp = client.post("/api/sessions/create", json={"name": "Update Test"})
        data = json.loads(create_resp.data)
        sid = data.get("session_id") or data.get("session", {}).get("id")
        resp = client.post("/api/sessions/save", json={"id": sid, "name": "Updated Name"})
        assert resp.status_code == 200

    def test_delete_session(self, client):
        create_resp = client.post("/api/sessions/create", json={"name": "Delete Test"})
        data = json.loads(create_resp.data)
        sid = data.get("session_id") or data.get("session", {}).get("id")
        resp = client.post("/api/sessions/save", json={"id": sid, "deleted": True})
        assert resp.status_code == 200


class TestAPIAgent:
    def test_decompose(self, client):
        resp = client.post("/api/decompose", json={
            "prompt": "Analyse api_server.py",
            "template": "kodeanalyse",
            "lang": "en"
        })
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "tree" in data

    def test_decompose_with_files(self, client):
        resp = client.post("/api/decompose", json={
            "prompt": "Analyse the code",
            "files": [{"filename": "test.py", "content": "x = 1"}],
            "template": "kodeanalyse",
            "lang": "da"
        })
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "tree" in data

    def test_decompose_no_prompt(self, client):
        resp = client.post("/api/decompose", json={})
        assert resp.status_code == 400


class TestAPIRoot:
    def test_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_list_issues(self, client):
        resp = client.get("/api/issues")
        assert resp.status_code == 200
        import json
        data = json.loads(resp.data)
        assert "issues" in data
        assert "meta" in data
        assert data["success"] is True

    def test_list_issues_includes_active_risks(self, client):
        resp = client.get("/api/issues")
        assert resp.status_code == 200
        import json
        data = json.loads(resp.data)
        issues_by_id = {issue["id"]: issue for issue in data.get("issues", [])}
        risks_by_id = {risk["id"]: risk for risk in data.get("active_risks", [])}
        all_issues_by_id = {issue["id"]: issue for issue in data.get("all_issues", [])}
        assert issues_by_id
        assert risks_by_id.get("STAB-001")
        assert "STAB-001" in all_issues_by_id
        assert set(issues_by_id).issubset(all_issues_by_id)
        active_risk = all_issues_by_id["STAB-001"]
        assert active_risk.get("description") == active_risk.get("context")
        assert "agent_tasks.py" in ",".join(active_risk.get("affected_files", []))
        assert set(data.get("meta", {}).get("summary_by_active_risk_severity", {})).issubset({"critical", "high", "medium", "low"})

    def test_delete_nonexistent_issue(self, client):
        resp = client.delete("/api/issues/DOESNOTEXIST")
        assert resp.status_code == 404


class TestAPIPhaseChecks:
    """Tests for /api/phase-checks endpoint (deterministic phase auto-advance)."""

    def test_get_all_templates(self, client):
        resp = client.get("/api/phase-checks")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["success"] is True
        assert "templates" in data
        assert "refactor" in data["templates"]
        assert "Plan" in data["templates"]["refactor"]
        assert "Ekstraher" in data["templates"]["refactor"]

    def test_get_specific_template(self, client):
        resp = client.get("/api/phase-checks?template=refactor")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["success"] is True
        assert data["template"] == "refactor"
        assert "phases" in data
        assert "refactor" in data["phases"]

    def test_get_unknown_template_returns_empty(self, client):
        resp = client.get("/api/phase-checks?template=nonexistent_template")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["success"] is True
        assert data["phases"]["nonexistent_template"] == {}

    def test_plan_phase_check_format(self, client):
        resp = client.get("/api/phase-checks?template=refactor")
        data = json.loads(resp.data)
        plan_check = data["phases"]["refactor"]["Plan"]
        assert plan_check["spec"]["type"] == "files_from_plan"
        assert plan_check["spec"]["plan_path"] == "refactor_plan.md"
        assert plan_check["spec"]["min_files"] == 1
        assert "refactor_plan.md" in plan_check["description"]
        assert "FORMÅL" in plan_check["description"]

    def test_ekstraher_phase_check_format(self, client):
        resp = client.get("/api/phase-checks?template=refactor")
        data = json.loads(resp.data)
        ekstraher_check = data["phases"]["refactor"]["Ekstraher"]
        assert ekstraher_check["spec"]["type"] == "all_of"
        sub_types = [c.get("type") for c in ekstraher_check["spec"]["checks"]]
        assert "files_from_plan" in sub_types
        assert "symbols_covered" in sub_types
        symbols_spec = next(c for c in ekstraher_check["spec"]["checks"] if c["type"] == "symbols_covered")
        assert symbols_spec["source_file"] == "{source_file}"
        assert "FORMÅL" in ekstraher_check["description"]