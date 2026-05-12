import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

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