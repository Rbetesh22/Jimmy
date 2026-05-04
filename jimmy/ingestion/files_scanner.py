"""
Smart mass file scanner — walks directories and ingests all readable documents.
Skips: node_modules, .git, venvs, .venv, __pycache__, build artifacts, binaries.
Supports: PDF, DOCX, TXT, MD, CSV (as text).
"""
from pathlib import Path
from .base import Document
from .file import FileIngester

SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__",
    ".next", "dist", "build", ".cache", "vendor", "target",
    ".tox", "eggs", ".eggs", "bower_components", ".yarn",
    "Library", "Applications", ".Trash", "snap",
}

SUPPORTED_EXTS = {".pdf", ".docx", ".txt", ".md", ".rtf"}

# Skip files that are too small (likely empty/stub) or too large
MIN_BYTES = 200
MAX_BYTES = 50 * 1024 * 1024  # 50MB

# Skip binary-looking filenames
SKIP_NAME_PATTERNS = [
    ".DS_Store", "Thumbs.db", ".localized", "desktop.ini",
]


def _should_skip_path(path: Path) -> bool:
    for part in path.parts:
        if part in SKIP_DIRS or part.startswith("."):
            return True
    return False


class FileScannerIngester:
    """Recursively scan directories and ingest all supported documents."""

    def scan(
        self,
        directories: list[str],
        on_progress=None,
    ) -> tuple[list[Document], dict]:
        """
        Scan directories and return (docs, stats).
        on_progress(path, status) called for each file attempted.
        """
        file_ingester = FileIngester()
        docs = []
        stats = {"found": 0, "ingested": 0, "skipped": 0, "failed": 0, "by_ext": {}}

        all_files = []
        for directory in directories:
            root = Path(directory).expanduser()
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if _should_skip_path(path):
                    continue
                if path.name in SKIP_NAME_PATTERNS:
                    continue
                if path.suffix.lower() not in SUPPORTED_EXTS:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size < MIN_BYTES or size > MAX_BYTES:
                    stats["skipped"] += 1
                    continue
                all_files.append(path)

        stats["found"] = len(all_files)

        for path in all_files:
            ext = path.suffix.lower()
            try:
                file_docs = file_ingester.ingest(str(path))
                mtime = ""
                try:
                    from datetime import datetime as _dt
                    mtime = _dt.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
                except Exception:
                    pass
                for doc in file_docs:
                    doc.source = "file"
                    doc.metadata["source"] = "file"
                    doc.metadata["file_path"] = str(path)
                    doc.metadata["file_ext"] = ext
                    doc.metadata["date"] = mtime
                docs.extend(file_docs)
                stats["ingested"] += 1
                stats["by_ext"][ext] = stats["by_ext"].get(ext, 0) + 1
                if on_progress:
                    on_progress(str(path), "ok")
            except Exception as e:
                stats["failed"] += 1
                if on_progress:
                    on_progress(str(path), f"error: {e}")

        return docs, stats
