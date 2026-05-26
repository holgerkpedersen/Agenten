"""Centralized configuration constants for Agenten."""

# Chunking
CHUNK_SIZE = 150000

# Folder scanning
FOLDER_SCAN_MAX_FILES = 20
FOLDER_SCAN_MAX_DEPTH = 2

# LLM defaults (used by Agent.__init__)
MAX_TOKENS = 16000
MAX_CONVERSATION_CHARS = 32000

# LLM connection (env vars LM_HOST/LM_PORT/LM_MODEL override these)
LLM_BASE_URL = "http://localhost:1234/v1"
LLM_MODEL = "qwen/qwen3.5-9b"

# Task execution
EXECUTION_TIMEOUT = 1800  # 30 min wall-clock per task
SUBPROCESS_TIMEOUT = 120  # seconds per subprocess call (pytest, git, etc.)
MAX_TOOL_CALLS = 6  # max LLM tool calls per task step
