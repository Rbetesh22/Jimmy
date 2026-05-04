import re
from pathlib import Path
from .base import Document
from .file import FileIngester

SUPPORTED = {".pdf", ".txt", ".md", ".docx", ".rtf", ".csv"}

# Notion exports filenames like "My Page abc123def456.md" — strip the UUID suffix
_NOTION_UUID = re.compile(r'\s+[0-9a-f]{32}$')


def _clean_title(stem: str) -> str:
    return _NOTION_UUID.sub('', stem).strip()


class FolderIngester:
    def ingest(self, folder_path: str, recursive: bool = True, source: str = "folder") -> list[Document]:
        p = Path(folder_path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Folder not found: {folder_path}")

        file_ingester = FileIngester()
        pattern = "**/*" if recursive else "*"
        files = [f for f in p.glob(pattern) if f.is_file() and f.suffix.lower() in SUPPORTED]

        docs = []
        for f in files:
            try:
                d = file_ingester.ingest(str(f))
                mtime = ""
                try:
                    from datetime import datetime as _dt
                    mtime = _dt.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
                except Exception:
                    pass
                for doc in d:
                    # Use cleaned title (strips Notion UUIDs)
                    doc.title = _clean_title(f.stem)
                    doc.source = source
                    doc.metadata["source"] = source
                    doc.metadata["file_path"] = str(f)
                    doc.metadata["date"] = mtime
                docs.extend(d)
                print(f"  ✓ {f.name}")
            except Exception as e:
                print(f"  ✗ {f.name}: {e}")
        return docs
