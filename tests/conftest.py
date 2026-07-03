import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(autouse=True)
def _reset_shared_globals():
    """STAB-005: Reset shared global state before every test to prevent leaks
    across test files. The agent singleton, session state, and execution globals
    persist at module level and must be cleared between unrelated tests."""
    from session_manager import session_manager, agent
    from stream_execution import _active_session_executions, _active_session_executions_lock

    session_manager.current_session_id = None
    agent.task_tree = None
    agent.agent_log = []
    agent.execution_log = []
    agent.file_chunks = {}
    agent.issue_resolved = False
    agent.active_template = None
    agent.current_phase = None

    with _active_session_executions_lock:
        _active_session_executions.clear()


@pytest.fixture
def test_session_dir(tmp_path):
    d = tmp_path / "sessions"
    d.mkdir()
    return d

@pytest.fixture
def sample_lang_keys():
    from i18n import K
    return {
        "K.TOOL_GITHUB_CREATE_REPO": K.TOOL_GITHUB_CREATE_REPO,
        "K.LOG_DECOMPOSE_START": K.LOG_DECOMPOSE_START,
        "K.UI_TITLE": K.UI_TITLE,
        "K.FT_UNDERSTAND_PURPOSE": K.FT_UNDERSTAND_PURPOSE,
        "K.SYS_ERROR_PREFIX": K.SYS_ERROR_PREFIX,
        "K.DEMO_MATH_FACT": K.DEMO_MATH_FACT,
        "K.DEMO_OPTIMIZATION": K.DEMO_OPTIMIZATION,
        "K.DEMO_KNOWLEDGE_HDR": K.DEMO_KNOWLEDGE_HDR,
    }

@pytest.fixture
def all_langs():
    return ["da", "en", "es", "zh"]

@pytest.fixture
def session_manager(test_session_dir):
    from session_manager import SessionManager
    return SessionManager(storage_dir=str(test_session_dir))