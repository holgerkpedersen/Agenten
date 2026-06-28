"""Centralized configuration constants for Agenten."""
import logging
import os
import sys
import threading
from typing import Any
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from datetime import datetime

# Force UTF-8 on Windows consoles at import time
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

def setup_logging() -> None:
    """Configure logging with console + file handlers. Call once at startup."""
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s',
                            datefmt='%H:%M:%S')
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Console handler — explicit UTF-8 encoding
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    try:
        ch.setStream(sys.stdout)
    except AttributeError:
        pass
    root.addHandler(ch)

    # File handler — UTF-8 by default on Python 3
    try:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.FileHandler(os.path.join(log_dir, 'agenten.log'), encoding='utf-8')
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass

def get_logger(name: str) -> logging.Logger:
    """get logger.
    
    Args:
        name:"""
    return logging.getLogger(name)

# Chunking
CHUNK_SIZE = 60000

# Folder scanning
FOLDER_SCAN_MAX_FILES = 20
FOLDER_SCAN_MAX_DEPTH = 2

# LLM defaults (used by Agent.__init__)
MAX_TOKENS = 32000
MAX_CONVERSATION_CHARS = 64000

# LLM connection
# LM_HOST / LM_PORT: host and port for LM Studio (e.g. localhost:1234).
# LLM_BASE_URL: full URL for chat completions — overrides LM_HOST/LM_PORT.
# LLM_MODEL: default model identifier (overridable via LM_MODEL env).
_LM_HOST_RAW = os.environ.get('LM_HOST', '')
_LM_PORT = os.environ.get('LM_PORT', '1234')
if '://' in _LM_HOST_RAW:
    # User passed full URL as LM_HOST — treat it as LLM_BASE_URL instead
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', _LM_HOST_RAW.rstrip('/'))
    LM_HOST = 'localhost'
else:
    LM_HOST = _LM_HOST_RAW or 'localhost'
    LLM_BASE_URL = os.environ.get('OPENCODE_BASE_URL') or os.environ.get('LLM_BASE_URL') or os.environ.get('LM_BASE_URL') or f'http://{LM_HOST}:{_LM_PORT}/v1'
LM_PORT = _LM_PORT
LLM_MODEL = os.environ.get("LM_MODEL", "qwen3.5-9b-mtp")
LLM_STREAM_TIMEOUT = 900
LLM_CONNECT_TIMEOUT = 120

# Image upload
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB max per image file

# Task execution
EXECUTION_TIMEOUT = 1800  # 30 min wall-clock per task
SUBPROCESS_TIMEOUT = 120  # seconds per subprocess call (pytest, git, etc.)
MAX_TOOL_CALLS_ANALYSE = 10   # Analyse/Læs/Afklar faser (læs issue + kode + kør tests)
MAX_TOOL_CALLS_FIX = 50       # Implementering/Fix/Test-faser med edit→test loop (50 for komplekse projekter)
MAX_TOOL_CALLS_CLOSE = 4      # Opdatering/Luk/Verifikation faser
MAX_FIX_ATTEMPTS = 3          # Max edit+test forsøg før opgivet
MAX_TASK_ITERATIONS = 6       # max LLM conversation turns per task
MAX_PR_TASK_ITERATIONS = 10   # max turns for PR/git workflow tasks
NATIVE_TOOLS = True  # Use OpenAI native function calling when available

# Per-template, per-phase iteration limits (LLM conversation turns).
# Different templates need different budgets: refactor Ekstraher needs 15+
# to create 7 modules, but a simple bugfix Analyse only needs 4.
# Override via instructions/iteration_limits.json (template.phase → limit).
# Falls back to MAX_TASK_ITERATIONS if not specified for a phase.
TEMPLATE_PHASE_ITERATION_LIMITS: dict[str, dict[str, int]] = {
    "kodeanalyse": {
        "Form\u00e5l": 8,
        "Imports og afh\u00e6ngigheder": 8,
        "Arkitektur": 8,
        "Kodekvalitet": 8,
        "Sikkerhed": 8,
    },
    "programmering": {
        "Kravanalyse": 8,
        "Arkitekturdesign": 10,
        "Implementeringsplan": 8,
        "Sikkerhedsanalyse": 8,
        "Uddyb/refinements": 15,
        "Kodeimplementering": 20,
    },
    "refactor": {
        "Analyse": 12,
        "Plan": 8,
        "Ekstraher": 20,
        "Opdat\u00e9r": 20,
        "Test": 8,
    },
    "bugfix": {
        "Analyse": 6,
        "Test (Red)": 6,
        "Implementering": 12,
        "Verifikation (Green)": 8,
        "Opdatering": 4,
    },
    "selvforbedring": {
        "Analyser": 6,
        "Diagnostic\u00e9r": 6,
        "Ret": 15,
        "Verific\u00e9r": 8,
        "Commit": 4,
    },
    "testgenerering": {
        "Analyse": 6,
        "Test (Red)": 8,
        "Implementering": 10,
        "Verifikation (Green)": 8,
    },
    "issue_handler": {
        "L\u00e6s": 4,
        "Afklar": 8,
        "Fix": 12,
        "Luk Issue": 4,
    },
}


def _load_iteration_limit_overrides() -> None:
    """Merge overrides from instructions/iteration_limits.json into TEMPLATE_PHASE_ITERATION_LIMITS.

    File format: {"template": {"Phase": 25, ...}, ...}
    Unknown templates/phases are silently ignored (no crash on malformed JSON).
    """
    import json as _json
    _override_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "instructions", "_iteration_limits.json"
    )
    try:
        with open(_override_path, encoding="utf-8") as _f:
            _overrides = _json.load(_f)
    except (OSError, _json.JSONDecodeError):
        return  # no override file or malformed — keep defaults
    if not isinstance(_overrides, dict):
        return
    for _template, _phases in _overrides.items():
        if _template not in TEMPLATE_PHASE_ITERATION_LIMITS:
            continue
        if not isinstance(_phases, dict):
            continue
        for _phase, _limit in _phases.items():
            if not isinstance(_limit, int) or _limit < 1:
                continue
            # Case-insensitive phase lookup in the defaults
            _canonical = None
            for _key in TEMPLATE_PHASE_ITERATION_LIMITS[_template]:
                if _key.lower() == _phase.lower():
                    _canonical = _key
                    break
            if _canonical:
                TEMPLATE_PHASE_ITERATION_LIMITS[_template][_canonical] = _limit
            else:
                # New phase key — add it
                TEMPLATE_PHASE_ITERATION_LIMITS[_template][_phase] = _limit


# Load overrides at import time (runs once)
_load_iteration_limit_overrides()

# Message size limits (chars) — prevents "too much context" errors to LM Studio
MAX_MESSAGE_CHARS = 60000  # hard cap on total message body sent per LLM call (raised from 20000 to preserve full conversation history)

# File context limits — prevent LM Studio HTTP timeout from large payloads
MAX_FILE_CONTEXT_CHARS = 4000  # max chars per file in initial system prompt (agent can read_chunk for more)

# Model-specific channel tags for decomposition prompts (e.g., Gemma requires <|channel|> tags)
# Key: model name substring (lowercase), Value: channel tag string appended to decomposition prompt
CHANNEL_TAG_MODELS = {
    "gemma": "\n<|channel>thought\n<channel|>",
}



def get_config(key: str, default: Any = None) -> Any:
    """Get a configuration value by key."""
    config = {
        "database_url": DATABASE_URL,
        "max_retries": MAX_RETRIES,
        "timeout": DEFAULT_TIMEOUT,
        "log_level": LOG_LEVEL,
    }
    return config.get(key, default)



def is_extension_allowed(ext: str) -> bool:
    """Check if a file extension is in the allowed list."""
    return ext.lower() in ALLOWED_EXTENSIONS



def validate_config() -> list[str]:
    """Validate configuration and return list of warnings."""
    warnings = []
    if MAX_RETRIES < 1:
        warnings.append("MAX_RETRIES should be at least 1")
    if DEFAULT_TIMEOUT < 5:
        warnings.append("DEFAULT_TIMEOUT is very low")
    if not ALLOWED_EXTENSIONS:
        warnings.append("ALLOWED_EXTENSIONS is empty")
    return warnings



def get_allowed_extensions() -> set[str]:
    """Return a copy of the allowed extensions set."""
    return set(ALLOWED_EXTENSIONS)



def config_to_dict() -> dict[str, Any]:
    """Return all configuration as a dictionary."""
    return {
        "database_url": DATABASE_URL,
        "max_retries": MAX_RETRIES,
        "timeout": DEFAULT_TIMEOUT,
        "allowed_extensions": list(ALLOWED_EXTENSIONS),
        "log_level": LOG_LEVEL,
    }



log = get_logger(__name__)


# ============ KONFIGURATION ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATIC_DIR = os.path.join(BASE_DIR, 'static')


app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='/static')


VERSION_FILES = ["api_server.py", "agent_core.py", "llm_wrapper.py", "tools.py", "lang.py", "i18n.py"]


# ============ VERSION ============
def _file_mtime(path: str) -> str:
    """file mtime.

    Args:
        path:"""
    try: return datetime.fromtimestamp(os.path.getmtime(os.path.join(BASE_DIR, path))).strftime("%H:%M:%S")
    except OSError: return "?"


BUILD_INFO = {f: _file_mtime(f) for f in VERSION_FILES}


# ============ SECURITY CONFIGURATION ============
def _is_development_mode() -> bool:
    """Check if server is running in development mode."""
    return os.environ.get('DEV_MODE', 'true').lower() == 'true'


# ============ SHARED GLOBAL STATE ============
active_streams: dict = {}
active_streams_lock = threading.Lock()
current_session_lock = threading.Lock()
