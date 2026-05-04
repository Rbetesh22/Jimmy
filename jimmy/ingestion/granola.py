import csv
import re
from pathlib import Path
from .base import Document, _h


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


class GranolaIngester:
    def ingest_csv(self, csv_path: str) -> list[Document]:
        docs = []
        with open(csv_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                doc_id = row.get("document_id", "")
                title = row.get("document_title", "Meeting")
                date = row.get("document_created", "")[:10]
                summary = _strip_html(row.get("summary", ""))
                notes = _strip_html(row.get("notes", ""))

                content = "\n\n".join(filter(None, [summary, notes]))
                if len(content) < 50:
                    continue

                docs.append(Document(
                    id=f"granola_{doc_id or _h(title + date)}",
                    content=content,
                    source="granola",
                    title=f"Meeting: {title} ({date})",
                    metadata={
                        "type": "meeting",
                        "date": date,
                        "title": title,
                    },
                ))
        return docs

    def ingest_all(self) -> list[Document]:
        """Ingest all Granola CSV exports found in ~/Personal."""
        docs = []
        search_dirs = [
            Path.home() / "Personal",
            Path.home() / "Documents",
            Path.home() / "Downloads",
        ]
        for d in search_dirs:
            for csv_file in d.glob("granola-export-*.csv"):
                docs.extend(self.ingest_csv(str(csv_file)))
        return docs
