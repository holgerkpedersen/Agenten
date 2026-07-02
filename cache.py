from typing import Any, Dict, List, Tuple, Optional
import os

# Cache for file content to prevent repeated reading of the same file
_FILE_CONTENT_CACHE: Dict[str, Tuple[str, float]] = {}

_CONTENT_CACHE_MAX_SIZE = 100  # Maximum number of files to cache

def _is_file_cached(filepath: str) -> bool:
    """Check if file is in cache and not expired."""
    if filepath not in _FILE_CONTENT_CACHE:
        return False

    content, timestamp = _FILE_CONTENT_CACHE[filepath]
    try:
        # Check if file has been modified
        if os.path.getmtime(filepath) != timestamp:
            # File has been modified, remove from cache
            del _FILE_CONTENT_CACHE[filepath]
            return False
        return True
    except OSError:
        # File no longer exists, remove from cache
        if filepath in _FILE_CONTENT_CACHE:
            del _FILE_CONTENT_CACHE[filepath]
        return False

def _cache_file_content(filepath: str, content: str) -> None:
    """Cache file content with LRU eviction when cache is too large."""
    global _FILE_CONTENT_CACHE

    # Remove if already exists
    if filepath in _FILE_CONTENT_CACHE:
        del _FILE_CONTENT_CACHE[filepath]

    # Add to cache
    try:
        _FILE_CONTENT_CACHE[filepath] = (content, os.path.getmtime(filepath))
    except OSError:
        # If we can't get the file's mtime, don't cache it
        return

    # Enforce maximum size by removing oldest entries
    if len(_FILE_CONTENT_CACHE) > _CONTENT_CACHE_MAX_SIZE:
        # Sort by timestamp (oldest first) and remove excess
        sorted_items = sorted(_FILE_CONTENT_CACHE.items(), key=lambda x: x[1][1])
        for key, _ in sorted_items[:-_CONTENT_CACHE_MAX_SIZE]:
            del _FILE_CONTENT_CACHE[key]

def _get_cached_file_content(filepath: str) -> Optional[str]:
    """Get cached file content if available and not expired."""
    if _is_file_cached(filepath):
        return _FILE_CONTENT_CACHE[filepath][0]
    return None

def _clear_file_content_cache() -> None:
    """Clear the file content cache."""
    global _FILE_CONTENT_CACHE
    _FILE_CONTENT_CACHE.clear()
