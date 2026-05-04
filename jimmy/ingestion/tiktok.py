import json
from pathlib import Path
from .base import Document


class TikTokIngester:
    def ingest(self, path: str) -> list[Document]:
        """Parse user_data.json from TikTok data export."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        docs = []
        activity = data.get("Activity", {})

        # Liked videos
        liked_section = activity.get("Like List", activity.get("VideoList", {}))
        liked_videos = liked_section.get("ItemFavoriteList", liked_section.get("VideoList", []))
        for item in liked_videos:
            link = item.get("Link", item.get("link", ""))
            date = item.get("Date", item.get("date", ""))
            if not link:
                continue
            content = f"Liked TikTok video: {link}"
            if date:
                content += f"\nDate: {date}"
            docs.append(Document(
                id=f"tiktok_liked_{_h(link)}",
                content=content,
                source="tiktok",
                title=f"TikTok Liked: {link[-60:]}",
                metadata={"type": "liked_video", "url": link, "date": date},
            ))

        # Browsing history (videos watched)
        browse_section = activity.get("Video Browsing History", activity.get("BrowsingHistory", {}))
        watched = browse_section.get("VideoList", [])
        for item in watched:
            link = item.get("Link", item.get("link", ""))
            date = item.get("Date", item.get("date", ""))
            if not link:
                continue
            content = f"Watched TikTok video: {link}"
            if date:
                content += f"\nDate: {date}"
            docs.append(Document(
                id=f"tiktok_watched_{_h(link + str(date))}",
                content=content,
                source="tiktok",
                title=f"TikTok Watched: {link[-60:]}",
                metadata={"type": "watched_video", "url": link, "date": date},
            ))

        # Search history (interesting for understanding interests)
        search_section = activity.get("Search History", {})
        searches = search_section.get("SearchList", [])
        if searches:
            # Group all searches into one document
            search_lines = []
            for item in searches:
                term = item.get("SearchTerm", item.get("term", ""))
                date = item.get("Date", item.get("date", ""))
                if term:
                    search_lines.append(f"- {term} ({date})" if date else f"- {term}")
            if search_lines:
                docs.append(Document(
                    id="tiktok_searches",
                    content="TikTok Search History:\n" + "\n".join(search_lines),
                    source="tiktok",
                    title="TikTok: My Searches",
                    metadata={"type": "search_history"},
                ))

        return docs
