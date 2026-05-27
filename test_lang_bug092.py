"""Test for BUG-092: lang.py nested key lookup silently falls back to unrelated ui section."""
import pytest
from unittest.mock import patch, MagicMock
import sys
import os

# Ensure we test the local lang module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import after path manipulation
try:
    from lang import t, LANG
except ImportError:
    # Fallback if direct import fails due to structure, though usually fine in tests
    pass

@pytest.fixture
def mock_lang_data():
    """Create a mock language structure that triggers the bug."""
    return {
        "da": {
            "log": {
                "task_start": "Opgave startet",
                # 'nonexistent' is missing here
            },
            "ui": {
                "title": "Agenten",
                # This key mimics the bug: a flat key in ui that matches a failed nested lookup
                "log.nonexistent": "UI Fallback Value (WRONG)", 
            }
        }
    }

def test_nested_key_fallback_to_ui_is_bug(mock_lang_data):
    """
    BUG-092: When looking up 'log.nonexistent', if 'nonexistent' is not in 'log',
    the code incorrectly checks if the full string 'log.nonexistent' exists in 'ui'.
    It should return '?log.nonexistent' instead.
    """
    # Patch LANG to use our mock data
    with patch('lang.LANG', mock_lang_data["da"]):
        result = t("log.nonexistent", lang="da")
        
        # The bug causes this to return the UI value
        # We expect it to return the missing key marker
        assert result == "?log.nonexistent", f"Expected '?log.nonexistent', got '{result}'. Bug BUG-092 is present: fallback to ui section occurred."

def test_valid_nested_key_works(mock_lang_data):
    """Ensure normal nested lookups still work."""
    with patch('lang.LANG', mock_lang_data["da"]):
        result = t("log.task_start", lang="da")
        assert result == "Opgave startet"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
