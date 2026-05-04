import re
import feedparser
from .base import Document


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


class RSSIngester:
    def ingest(self, url: str, limit: int = 50) -> list[Document]:
        feed = feedparser.parse(url)
        if not feed.entries:
            raise ValueError(f"No entries found in feed: {url}")

        feed_title = feed.feed.get("title", url)
        docs = []

        for entry in feed.entries[:limit]:
            title = entry.get("title", "Untitled")
            content = (
                entry.get("content", [{}])[0].get("value", "")
                or entry.get("summary", "")
                or entry.get("description", "")
            )
            content = _strip_html(content)
            if len(content) < 100:
                continue

            # Normalize published date to YYYY-MM-DD
            pub_date = ""
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                import time as _time
                try:
                    pub_date = _time.strftime("%Y-%m-%d", entry.published_parsed)
                except Exception:
                    pass
            if not pub_date:
                pub_date = entry.get("published", "")[:10]

            entry_id = entry.get("id", entry.get("link", title))
            docs.append(Document(
                id=f"rss_{_h(entry_id)}",
                content=f"{title}\n\n{content}",
                source="podcast",
                title=f"{feed_title}: {title}",
                metadata={
                    "type": "episode",
                    "feed": feed_title,
                    "date": pub_date,
                    "url": entry.get("link", ""),
                },
            ))

        return docs
