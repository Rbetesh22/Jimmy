"""
GoodNotes ingester.

Supports three modes:
1. Auto-discovery: scans iCloud Drive for GoodNotes auto-backup PDFs
2. Folder: ingests all PDFs/docs from a specified folder
3. .goodnotes file: unzips and extracts any embedded PDFs from the archive

Setup for best results:
  GoodNotes > Settings > Auto-backup > enable, set to iCloud Drive → GoodNotes folder
  Then: jimmy ingest goodnotes          (auto-discovers)
"""

import zipfile
import tempfile
import logging
from pathlib import Path
from datetime import datetime

from .base import Document, _h

log = logging.getLogger(__name__)

# iCloud paths where GoodNotes may store files
ICLOUD_SEARCH_PATHS = [
    # GoodNotes 5 iCloud container
    Path.home() / "Library" / "Mobile Documents" / "5UAR78B6YU~com~goodnotesapp~goodnotes" / "Documents",
    # GoodNotes 6 iCloud container (bundle ID may vary)
    Path.home() / "Library" / "Mobile Documents" / "com~goodnotesapp~GoodNotes-6" / "Documents",
    # Auto-backup target inside iCloud Drive
    Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "GoodNotes",
    Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "GoodNotes 6",
]


class GoodNotesIngester:
    """Ingest GoodNotes notebooks as searchable text."""

    def ingest(self, path: str | None = None) -> list[Document]:
        """
        If path is given, ingest from that file or folder.
        Otherwise auto-discover GoodNotes content from iCloud.
        """
        if path:
            p = Path(path).expanduser().resolve()
            if p.is_file():
                if p.suffix.lower() == ".goodnotes":
                    return self._ingest_goodnotes_zip(p)
                elif p.suffix.lower() == ".pdf":
                    return self._ingest_pdf(p)
                else:
                    return []
            elif p.is_dir():
                return self._scan_folder(p)
            else:
                raise FileNotFoundError(f"Not found: {path}")
        else:
            return self._auto_discover()

    # ── Auto-discovery ────────────────────────────────────────────────────────

    def _auto_discover(self) -> list[Document]:
        docs = []
        found_any = False
        for search_path in ICLOUD_SEARCH_PATHS:
            if search_path.exists():
                found_any = True
                print(f"  Scanning {search_path}")
                docs.extend(self._scan_folder(search_path))
        if not found_any:
            print("  No GoodNotes iCloud folder found.")
            print("  → In GoodNotes: Settings > Auto-backup > iCloud Drive > GoodNotes")
            print("  → Or run: jimmy ingest goodnotes <path-to-folder>")
        return docs

    # ── Folder scanner ────────────────────────────────────────────────────────

    def _scan_folder(self, folder: Path) -> list[Document]:
        docs = []
        # Handle .goodnotes archives
        for f in sorted(folder.rglob("*.goodnotes")):
            print(f"  Processing {f.name}…")
            d = self._ingest_goodnotes_zip(f)
            if d:
                docs.extend(d)
            else:
                print(f"    ↳ No text found — export from GoodNotes as PDF with text recognition")
        # Handle PDFs (exported notebooks or auto-backup)
        for f in sorted(folder.rglob("*.pdf")):
            print(f"  Processing {f.name}…")
            docs.extend(self._ingest_pdf(f))
        return docs

    # ── .goodnotes archive ────────────────────────────────────────────────────

    def _ingest_goodnotes_zip(self, path: Path) -> list[Document]:
        """
        .goodnotes files are ZIP archives. GoodNotes stores page data in a
        proprietary binary format (.page files), but may also contain embedded
        PDFs when the notebook was created from a PDF template.
        """
        docs = []
        try:
            with zipfile.ZipFile(path, "r") as z:
                names = z.namelist()
                pdf_entries = [n for n in names if n.lower().endswith(".pdf")]
                if not pdf_entries:
                    # No embedded PDFs — .page files are proprietary binary
                    return []
                with tempfile.TemporaryDirectory() as tmp:
                    for entry in pdf_entries:
                        z.extract(entry, tmp)
                        pdf_path = Path(tmp) / entry
                        # Use notebook name as title prefix
                        for doc in self._ingest_pdf(pdf_path, notebook_name=path.stem):
                            docs.append(doc)
        except (zipfile.BadZipFile, Exception) as e:
            log.debug("Could not read %s: %s", path, e)
        return docs

    # ── PDF extraction ────────────────────────────────────────────────────────

    def _ingest_pdf(self, path: Path, notebook_name: str | None = None) -> list[Document]:
        try:
            import pypdf
            logging.getLogger("pypdf").setLevel(logging.ERROR)
            reader = pypdf.PdfReader(str(path), strict=False)
            pages_text = []
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    pages_text.append(t.strip())
            text = "\n\n".join(pages_text).strip()
            if not text:
                return []

            title = notebook_name or path.stem
            mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
            return [Document(
                id=f"goodnotes_{_h(str(path))}",
                title=title,
                content=text,
                source="goodnotes",
                metadata={
                    "title": title,
                    "source": "goodnotes",
                    "path": str(path),
                    "date": mtime,
                    "pages": len(reader.pages),
                },
            )]
        except Exception as e:
            log.debug("PDF extraction failed for %s: %s", path, e)
            return []
