import pytest
import ddg_search
from ddg_search import search_ddg
from unittest.mock import patch, MagicMock


def test_search_ddg_handles_network_failure_gracefully():
    """When network request fails, returns [] after 3 retries — no crash."""
    with patch('ddg_search.urllib.request.urlopen', side_effect=Exception("Connection refused")):
        results = search_ddg("test query", max_results=5)
        assert results == []
        assert ddg_search.urllib.request.urlopen.call_count == 3


def test_search_ddg_does_not_silently_swallow_internal_errors():
    """Internal parsing errors should propagate, not be swallowed by a bare except."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"<html>dummy</html>"
    with patch('ddg_search.urllib.request.urlopen', return_value=mock_resp):
        with patch('ddg_search.re.findall', side_effect=TypeError("Simulated parsing error")):
            with pytest.raises(TypeError, match="Simulated parsing error"):
                search_ddg("test query", max_results=5)
