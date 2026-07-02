import os
import hashlib
import config



def file_hash(filepath: str) -> str | None:
    """file hash.

    Args:
        filepath:"""
    try:
        size = os.path.getsize(filepath)
        if size > config.MAX_IMAGE_SIZE * 2:
            return None
        h = hashlib.sha256()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except (IOError, OSError):
        return None
