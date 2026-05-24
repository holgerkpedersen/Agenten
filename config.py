"""Centralized configuration constants for Agenten."""

# Chunking
CHUNK_SIZE = 150000

# Folder scanning
FOLDER_SCAN_MAX_FILES = 20
FOLDER_SCAN_MAX_DEPTH = 2

# LLM defaults (used by Agent.__init__)
MAX_TOKENS = 16000
MAX_CONVERSATION_CHARS = 32000

# Task execution
EXECUTION_TIMEOUT = 1800  # 30 min wall-clock per task
