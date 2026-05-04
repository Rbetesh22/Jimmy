import httpx
from .base import Document


class PocketIngester:
    def __init__(self, consumer_key: str, access_token: str):
        self.consumer_key = consumer_key
        self.access_token = access_token

    def ingest(self, state: str = "all", count: int = 500) -> list[Document]:
        """Fetch saved articles from Pocket."""
        payload = {
            "consumer_key": self.consumer_key,
            "access_token": self.access_token,
            "state": state,
            "detailType": "complete",
            "count": count,
            "sort": "newest",
        }
        resp = httpx.post(
            "https://getpocket.com/v3/get",
            json=payload,
            headers={"Content-Type": "application/json", "X-Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        articles = data.get("list", {})
        if not isinstance(articles, dict):
            return []

        docs = []
        for item_id, item in articles.items():
            title = (item.get("resolved_title") or item.get("given_title") or "").strip()
            url = (item.get("resolved_url") or item.get("given_url") or "").strip()
            excerpt = (item.get("excerpt") or "").strip()
            tags = list(item.get("tags", {}).keys())
            time_added = item.get("time_added", "")

            if not title and not url:
                continue

            content_parts = []
            if title:
                content_parts.append(title)
            if url:
                content_parts.append(f"URL: {url}")
            if excerpt:
                content_parts.append(f"\n{excerpt}")
            if tags:
                content_parts.append(f"\nTags: {', '.join(tags)}")
            if time_added:
                from datetime import datetime
                try:
                    date_str = datetime.utcfromtimestamp(int(time_added)).strftime("%Y-%m-%d")
                    content_parts.append(f"Saved: {date_str}")
                except Exception:
                    pass

            saved_date = ""
            if time_added:
                from datetime import datetime as _dt
                try:
                    saved_date = _dt.utcfromtimestamp(int(time_added)).strftime("%Y-%m-%d")
                except Exception:
                    pass
            # status: "0"=unread, "1"=read/archived
            raw_status = str(item.get("status", "0"))
            status = "read" if raw_status == "1" else "unread"
            docs.append(Document(
                id=f"pocket_{item_id}",
                content="\n".join(content_parts),
                source="pocket",
                title=title or url,
                metadata={"type": "article", "url": url, "tags": tags, "date": saved_date, "status": status},
            ))

        return docs
