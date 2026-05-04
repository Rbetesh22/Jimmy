import hashlib
from dataclasses import dataclass, field


@dataclass
class Document:
    id: str
    content: str
    source: str
    title: str
    metadata: dict = field(default_factory=dict)


def _h(s: str) -> str:
    """Stable, deterministic 16-char hex ID fragment. Use instead of abs(hash(...))."""
    return hashlib.md5(s.encode("utf-8", errors="replace"), usedforsecurity=False).hexdigest()[:16]
