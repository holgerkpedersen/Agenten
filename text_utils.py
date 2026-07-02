import config

CHUNK_SIZE = config.CHUNK_SIZE



def chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """chunk text.

    Args:
        text:
        size:"""
    chunks = []
    for i in range(0, len(text), size):
        chunks.append(text[i:i + size])
    return chunks
