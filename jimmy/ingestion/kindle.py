from pathlib import Path
from .base import Document

# Default Kindle clippings locations
DEFAULT_PATHS = [
    Path("/Volumes/Kindle/documents/My Clippings.txt"),
    Path.home() / "Documents/My Clippings.txt",
    Path.home() / "Downloads/My Clippings.txt",
]


class KindleIngester:
    def ingest(self, path: str | None = None) -> list[Document]:
        if path:
            p = Path(path).expanduser()
        else:
            p = next((x for x in DEFAULT_PATHS if x.exists()), None)
            if not p:
                raise FileNotFoundError(
                    "Kindle clippings not found. Connect your Kindle or pass --path"
                )

        import re
        from datetime import datetime

        raw = p.read_text(encoding="utf-8-sig", errors="ignore")
        entries = raw.split("==========")

        # {book: {"highlights": [(text, date_str)], "most_recent": ""}}
        highlights_by_book: dict[str, dict] = {}

        for entry in entries:
            lines = [l.strip() for l in entry.strip().split("\n") if l.strip()]
            if len(lines) < 3:
                continue
            book = lines[0]
            meta_line = lines[1] if len(lines) > 1 else ""
            text = "\n".join(lines[2:]).strip()
            if not text:
                continue

            # Parse "Added on Sunday, March 5, 2023 8:45:17 PM"
            date_str = ""
            m = re.search(r"Added on (.+)$", meta_line)
            if m:
                try:
                    dt = datetime.strptime(m.group(1).strip(), "%A, %B %d, %Y %I:%M:%S %p")
                    date_str = dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass

            if book not in highlights_by_book:
                highlights_by_book[book] = {"highlights": [], "most_recent": ""}
            highlights_by_book[book]["highlights"].append((text, date_str))
            if date_str and date_str > highlights_by_book[book]["most_recent"]:
                highlights_by_book[book]["most_recent"] = date_str

        docs = []
        for book, data in highlights_by_book.items():
            # Build content with per-highlight dates where available
            parts = []
            for text, date_str in data["highlights"]:
                prefix = f"[{date_str}] " if date_str else ""
                parts.append(f"{prefix}{text}")
            docs.append(Document(
                id=f"kindle_{_h(book)}",
                content="\n\n".join(parts),
                source="kindle",
                title=f"Kindle: {book}",
                metadata={
                    "type": "book_highlights",
                    "book": book,
                    "date": data["most_recent"],
                },
            ))
        return docs
