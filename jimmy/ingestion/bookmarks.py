import json
from pathlib import Path
from .base import Document
from .web import WebIngester

CHROME_PATH = Path.home() / "Library/Application Support/Google/Chrome/Default/Bookmarks"
SAFARI_PATH = Path.home() / "Library/Safari/Bookmarks.plist"


class BookmarksIngester:
    def ingest_chrome(self, fetch_content: bool = True, limit: int = 50) -> list[Document]:
        if not CHROME_PATH.exists():
            raise FileNotFoundError("Chrome bookmarks not found")

        with open(CHROME_PATH) as f:
            data = json.load(f)

        urls: list[tuple[str, str, str]] = []
        self._walk(data.get("roots", {}), urls)

        if not fetch_content:
            # Return just bookmark metadata as text
            content = "\n".join(
                f"{name}: {url}" + (f" (bookmarked {date})" if date else "")
                for url, name, date in urls
            )
            return [Document(
                id="chrome_bookmarks",
                content=content,
                source="bookmarks",
                title="Chrome Bookmarks",
                metadata={"type": "bookmarks", "count": len(urls)},
            )]

        # Fetch and ingest each page; patch date into the web doc metadata
        docs = []
        web = WebIngester()
        for url, name, date in urls[:limit]:
            try:
                page_docs = web.ingest(url)
                for doc in page_docs:
                    if date and not doc.metadata.get("date"):
                        doc.metadata["date"] = date
                docs.extend(page_docs)
                print(f"  ✓ {name[:70]}")
            except Exception as e:
                print(f"  ✗ {name[:60]}: {e}")
        return docs

    def _walk(self, node: dict, urls: list[tuple[str, str, str]]):
        if node.get("type") == "url":
            # Chrome stores date_added as microseconds since Jan 1, 1601
            date_str = ""
            raw_date = node.get("date_added", "")
            if raw_date:
                try:
                    from datetime import datetime, timedelta
                    epoch = datetime(1601, 1, 1) + timedelta(microseconds=int(raw_date))
                    date_str = epoch.strftime("%Y-%m-%d")
                except Exception:
                    pass
            urls.append((node.get("url", ""), node.get("name", ""), date_str))
        for child in node.get("children", []):
            self._walk(child, urls)
        for key in ("bookmark_bar", "other", "synced"):
            if key in node:
                self._walk(node[key], urls)
