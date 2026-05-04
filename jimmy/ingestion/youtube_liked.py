"""
Ingest YouTube liked videos from Google Takeout JSON export.

Export steps:
1. Go to https://takeout.google.com
2. Deselect all, then select "YouTube and YouTube Music"
3. Under options, choose "liked videos" playlist
4. Download and extract the ZIP
5. Find: Takeout/YouTube and YouTube Music/playlists/Liked videos.json
   or:   Takeout/YouTube and YouTube Music/history/watch-history.json

Run: jimmy ingest youtube-liked <path-to-json>
"""
import json
from pathlib import Path
from .base import Document
from .youtube import YouTubeIngester


class YouTubeLikedIngester:
    """Ingest YouTube liked videos playlist from Google Takeout JSON."""

    def ingest_from_takeout(self, json_path: str, limit: int = 200) -> list[Document]:
        """Parse Google Takeout liked videos JSON and fetch transcripts."""
        data = json.loads(Path(json_path).read_text())

        # Handle both playlist format and watch history format
        videos = []
        if isinstance(data, list):
            # Watch history format: list of {titleUrl, title, time, ...}
            for item in data:
                url = item.get("titleUrl", "")
                title = item.get("title", "")
                time_str = item.get("time", "")  # ISO 8601, e.g. "2024-03-05T14:22:00.000Z"
                liked_date = time_str[:10] if time_str else ""
                if "youtube.com/watch" in url or "youtu.be" in url:
                    videos.append({"url": url, "title": title, "date": liked_date})
        elif isinstance(data, dict) and "items" in data:
            # Playlist format: {items: [{snippet: {resourceId: {videoId}, title}}]}
            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                vid_id = snippet.get("resourceId", {}).get("videoId", "")
                title = snippet.get("title", "")
                if vid_id:
                    videos.append({"url": f"https://youtube.com/watch?v={vid_id}", "title": title})

        docs = []
        ingester = YouTubeIngester()
        failed = 0

        for video in videos[:limit]:
            try:
                video_docs = ingester.ingest(video["url"])
                # Override source to distinguish liked videos
                for doc in video_docs:
                    doc.source = "youtube_liked"
                    doc.metadata["source"] = "youtube_liked"
                    if video.get("date") and not doc.metadata.get("date"):
                        doc.metadata["date"] = video["date"]
                docs.extend(video_docs)
            except Exception:
                failed += 1

        return docs, len(videos[:limit]), failed
