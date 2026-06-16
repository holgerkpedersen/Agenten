"""Routes module — extracted from api_server.py by REFAC session."""


def upload_file(filename):  # type: ignore
    """Upload a file."""
    pass


def read_file(filename):  # type: ignore
    """Read a file."""
    pass


def view_file(filename):  # type: ignore
    """View a file."""
    pass


def get_current_session():  # type: ignore
    """Get the current session ID."""
    from api_server import current_session_id
    return current_session_id
