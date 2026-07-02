import re

STUB_PATTERN = re.compile(
    r'def\s+(\w+)\(self[^)]*\):\s*\n\s+return\s+(\w+)\.\1\b'
)



def detect_delegations(content: str) -> list[tuple[str, str]]:
    """detect delegations.

    Args:
        content:"""
    stubs = []
    for m in STUB_PATTERN.finditer(content):
        stubs.append((m.group(1), m.group(2)))
    return stubs
