"""Debug script to reproduce the SSE hang."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from api_server import app as flask_app
import session_manager
from api_server import agent

# Simulate what test_api.py + test_paastande.py do
flask_app.config["TESTING"] = True
session_manager.current_session_id = None

with flask_app.test_client() as c:
    resp = c.post("/api/decompose", json={
        "prompt": "Analyse api_server.py",
        "template": "kodeanalyse",
        "lang": "en"
    })
    _ = resp.data
print("decompose done: %s" % resp.status_code)

# Import refactoring engine (like test_paastande.py does)
import refactoring_engine
print("refactoring_engine loaded")

# First SSE test - headers only
session_manager.current_session_id = None
agent.task_tree = None
agent.original_prompt = ""

with flask_app.test_client() as c:
    resp = c.get("/api/execute-stream")
    status = resp.status_code
    mimetype = resp.mimetype
    # NO resp.data! Just headers
print("SSE1 headers: %s %s" % (status, mimetype))

# Second SSE test - full
session_manager.current_session_id = None
agent.task_tree = None
agent.original_prompt = ""

t0 = time.time()
with flask_app.test_client() as c:
    resp = c.get("/api/execute-stream")
    t1 = time.time()
    _ = resp.data
    t2 = time.time()
print("SSE2: %.3fs (headers) + %.3fs (data)" % (t1 - t0, t2 - t1))
print("status: %s, mimetype: %s" % (resp.status_code, resp.mimetype))
