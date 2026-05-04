import httpx
from .base import Document
from .enrich_book import enrich_book

BASE_URL = "https://readwise.io/api/v2"


class ReadwiseIngester:
    def __init__(self, api_token: str):
        self.headers = {"Authorization": f"Token {api_token}"}

    def ingest(self) -> list[Document]:
        books = self._get_all("/books/")
        highlights = self._get_all("/highlights/")

        by_book: dict[int, list[str]] = {}
        for h in highlights:
            text = h.get("text", "").strip()
            if text:
                by_book.setdefault(h["book_id"], []).append(text)

        book_map = {b["id"]: b for b in books}
        # Index highlights by book_id (with dates)
        by_book_full: dict[int, list[dict]] = {}
        for h in highlights:
            by_book_full.setdefault(h["book_id"], []).append(h)

        docs = []
        for book_id, texts in by_book.items():
            book = book_map.get(book_id, {})
            title = book.get("title", f"Book {book_id}")
            author = book.get("author", "")
            category = book.get("category", "highlight")
            hl_dates = [
                h.get("highlighted_at", "") or ""
                for h in by_book_full.get(book_id, [])
            ]
            most_recent = max((d[:10] for d in hl_dates if d), default="")
            docs.append(Document(
                id=f"readwise_{book_id}",
                content="\n\n".join(texts),
                source="readwise",
                title=f"{title}" + (f" — {author}" if author else ""),
                metadata={
                    "type": category,
                    "title": title,
                    "author": author,
                    "source_url": book.get("source_url", ""),
                    "date": most_recent,
                    "status": "consumed",   # highlights = you read it
                },
            ))
            # For books with few highlights, enrich with Wikipedia summary
            if category == "books" and len(texts) < 5:
                wiki_doc = enrich_book(title, author)
                if wiki_doc:
                    docs.append(wiki_doc)
        return docs

    def _get_all(self, endpoint: str) -> list[dict]:
        results = []
        url = f"{BASE_URL}{endpoint}"
        while url:
            r = httpx.get(url, headers=self.headers, params={"page_size": 1000}, timeout=30)
            r.raise_for_status()
            data = r.json()
            results.extend(data.get("results", []))
            url = data.get("next")
        return results
