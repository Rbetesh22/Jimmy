"""Google Takeout — YouTube watch & search history ingester."""
import json
from pathlib import Path
from .base import Document, _h


class YouTubeHistoryIngester:
    def ingest(self, takeout_path: str) -> list[Document]:
        """Parse YouTube history from Google Takeout.

        takeout_path: path to Takeout/ folder, or directly to watch-history.json
        """
        p = Path(takeout_path)
        docs = []

        # Find watch history
        watch_candidates = [
            p / "YouTube and YouTube Music" / "history" / "watch-history.json",
            p / "YouTube" / "history" / "watch-history.json",
            p / "watch-history.json",
            p,
        ]
        for candidate in watch_candidates:
            if candidate.is_file() and candidate.suffix == ".json":
                docs.extend(self._parse_watch_history(candidate))
                break

        # Find search history
        search_candidates = [
            p / "YouTube and YouTube Music" / "history" / "search-history.json",
            p / "YouTube" / "history" / "search-history.json",
            p / "search-history.json",
        ]
        for candidate in search_candidates:
            if candidate.is_file():
                docs.extend(self._parse_search_history(candidate))
                break

        return docs

    def _parse_watch_history(self, path: Path) -> list[Document]:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        docs = []
        for item in data:
            title = item.get("title", "").replace("Watched ", "", 1).strip()
            if not title or len(title) < 5:
                continue

            channel = ""
            subtitles = item.get("subtitles", [])
            if subtitles:
                channel = subtitles[0].get("name", "")

            url = item.get("titleUrl", "")
            time_str = item.get("time", "")
            date = time_str[:10] if len(time_str) >= 10 else ""

            content = f"Watched: {title}"
            if channel:
                content += f"\nChannel: {channel}"
            if date:
                content += f"\nDate: {date}"

            docs.append(Document(
                id=f"yt_watch_{_h(title + date)}",
                content=content,
                source="youtube",
                title=f"Watched: {title[:80]}",
                metadata={"type": "watch_history", "date": date, "channel": channel, "url": url},
            ))
        return docs

    def _parse_search_history(self, path: Path) -> list[Document]:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        docs = []
        for item in data:
            title = item.get("title", "").replace("Searched for ", "", 1).strip()
            if not title or len(title) < 3:
                continue

            time_str = item.get("time", "")
            date = time_str[:10] if len(time_str) >= 10 else ""

            docs.append(Document(
                id=f"yt_search_{_h(title + date)}",
                content=f"YouTube search: {title}\nDate: {date}",
                source="youtube",
                title=f"Searched: {title[:80]}",
                metadata={"type": "search_history", "date": date},
            ))
        return docs
