"""Test that session_manager uses absolute path independent of CWD."""
import os
import json
import tempfile
from session_manager import SessionManager


def test_session_manager_default_is_relative():
    """Uden -w bruger SessionManager relativ 'sessions' sti."""
    sm = SessionManager()
    assert sm.storage_dir == "sessions", \
        f"Forventet 'sessions', fik {sm.storage_dir}"


def test_session_manager_absolute_path_independent_of_cwd():
    """SessionManager med absolut sti skal finde sessions i den angivne mappe,
    ikke i CWD."""
    original_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmpdir:
        agenten_dir = os.path.join(tmpdir, "Agenten")
        ocr_dir = os.path.join(tmpdir, "OCRScanner")
        agenten_sessions = os.path.join(agenten_dir, "sessions")
        ocr_sessions = os.path.join(ocr_dir, "sessions")

        os.makedirs(agenten_sessions)
        os.makedirs(ocr_sessions)

        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        test_data = {"original_prompt": "test prompt", "template": "kodeanalyse"}
        with open(os.path.join(agenten_sessions, f"{session_id}.json"), "w") as f:
            json.dump(test_data, f)

        os.chdir(ocr_dir)
        try:
            sm = SessionManager(os.path.join(agenten_dir, "sessions"))
            assert sm.storage_dir == agenten_sessions
            sessions = sm.list_sessions()
            ids = [s["id"] for s in sessions]
            assert session_id in ids
            data = sm.load_session(session_id)
            assert data is not None
            assert data["original_prompt"] == "test prompt"
        finally:
            os.chdir(original_cwd)
