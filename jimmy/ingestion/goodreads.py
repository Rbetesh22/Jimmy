import csv
from pathlib import Path
from .base import Document, _h
from .enrich_book import enrich_book


class GoodreadsIngester:
    def ingest(self, csv_path: str, enrich: bool = True) -> list[Document]:
        """Parse Goodreads library export CSV."""
        docs = []
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                shelf = row.get("Exclusive Shelf", row.get("Bookshelves", "")).strip().lower()
                # Include all shelves but mark reading status
                title = row.get("Title", "").strip()
                author = row.get("Author", row.get("Author l-f", "")).strip()
                if not title:
                    continue

                rating = row.get("My Rating", "0").strip()
                date_read = row.get("Date Read", "").strip()
                date_added = row.get("Date Added", "").strip()
                review = row.get("My Review", "").strip()
                num_pages = row.get("Number of Pages", "").strip()
                avg_rating = row.get("Average Rating", "").strip()
                year_pub = row.get("Original Publication Year", row.get("Year Published", "")).strip()

                lines = [f'"{title}" by {author}']
                if year_pub:
                    lines[0] += f" ({year_pub})"
                if shelf:
                    lines.append(f"Shelf: {shelf}")
                if rating and rating != "0":
                    lines.append(f"My rating: {rating}/5")
                if avg_rating:
                    lines.append(f"Average rating: {avg_rating}/5")
                if date_read:
                    lines.append(f"Date read: {date_read}")
                elif date_added:
                    lines.append(f"Date added: {date_added}")
                if num_pages:
                    lines.append(f"Pages: {num_pages}")
                if review:
                    lines.append(f"\nMy review:\n{review}")

                content = "\n".join(lines)
                # Map shelf to consumption status
                if shelf == "read":
                    status = "consumed"
                elif shelf == "currently-reading":
                    status = "in_progress"
                else:
                    status = "saved"

                doc_id = f"goodreads_{_h(title + author)}"
                docs.append(Document(
                    id=doc_id,
                    content=content,
                    source="goodreads",
                    title=f"{title} — {author}",
                    metadata={
                        "type": "book",
                        "shelf": shelf,
                        "rating": rating,
                        "date_read": date_read,
                        "status": status,
                    },
                ))

                # Enrich with Wikipedia summary for books not yet read
                if enrich and status in ("saved", "in_progress"):
                    wiki_doc = enrich_book(title, author)
                    if wiki_doc:
                        docs.append(wiki_doc)

        return docs
