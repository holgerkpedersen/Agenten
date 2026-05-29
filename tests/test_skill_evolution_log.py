import pytest
from unittest.mock import patch
import sys
import os

# Ensure project root is in path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from skill_evolution import _log_applied


def test_log_applied_catches_io_error_and_logs_warning(caplog):
    """
    Verify that _log_applied uses the logging module instead of print() 
    when an exception occurs during file I/O.
    """
    with caplog.at_level("WARNING"):
        # Simulate a file I/O error to trigger the except block
        with patch("skill_evolution.open", side_effect=IOError("Simulated disk full")):
            _log_applied([{"action": "retain", "skill": "test_skill"}])

        # Assert that the warning was captured by Python's logging module.
        # If print() is used instead, caplog.text will be empty and this fails.
        assert "Failed to log applied evolution actions" in caplog.text
