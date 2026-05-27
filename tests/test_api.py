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

    def test_delete_nonexistent_issue(self, client):
        resp = client.delete("/api/issues/DOESNOTEXIST")
        assert resp.status_code == 404