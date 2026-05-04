import httpx
from collections import defaultdict
from .base import Document, _h
from .enrich_book import enrich_book

TRAKT_API = "https://api.trakt.tv"
HEADERS_BASE = {
    "Content-Type": "application/json",
    "trakt-api-version": "2",
}


class TraktIngester:
    def __init__(self, client_id: str, username: str):
        self.client_id = client_id
        self.username = username
        self.headers = {**HEADERS_BASE, "trakt-api-key": client_id}

    def _get(self, path: str, params: dict = None) -> list:
        """Fetch all pages from a Trakt endpoint."""
        all_items = []
        page = 1
        while True:
            p = {"page": page, "limit": 100, **(params or {})}
            resp = httpx.get(f"{TRAKT_API}{path}", headers=self.headers, params=p, timeout=30)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            items = resp.json()
            if not items:
                break
            all_items.extend(items)
            total_pages = int(resp.headers.get("X-Pagination-Page-Count", 1))
            if page >= total_pages:
                break
            page += 1
        return all_items

    def ingest(self) -> list[Document]:
        docs = []
        docs.extend(self._ingest_movies())
        docs.extend(self._ingest_shows())
        docs.extend(self._ingest_ratings())
        docs.extend(self._ingest_watchlist())
        return docs

    def _ingest_movies(self) -> list[Document]:
        items = self._get(f"/users/{self.username}/watched/movies")
        docs = []
        for item in items:
            movie = item.get("movie", {})
            title = movie.get("title", "Unknown")
            year = movie.get("year", "")
            plays = item.get("plays", 1)
            last_watched = (item.get("last_watched_at") or "")[:10]

            content = f'Movie: "{title}"' + (f" ({year})" if year else "")
            content += f"\nWatches: {plays}"
            if last_watched:
                content += f"\nLast watched: {last_watched}"
            ids = movie.get("ids", {})
            if ids.get("tmdb"):
                content += f"\nTMDB: {ids['tmdb']}"

            docs.append(Document(
                id=f"trakt_movie_{ids.get('trakt', _h(title))}",
                content=content,
                source="trakt",
                title=f"{title}" + (f" ({year})" if year else ""),
                metadata={"type": "movie", "year": str(year), "plays": plays, "date": last_watched, "status": "consumed"},
            ))
        return docs

    def _ingest_shows(self) -> list[Document]:
        items = self._get(f"/users/{self.username}/watched/shows")
        docs = []
        for item in items:
            show = item.get("show", {})
            title = show.get("title", "Unknown")
            year = show.get("year", "")
            plays = item.get("plays", 1)
            last_watched = (item.get("last_watched_at") or "")[:10]
            seasons = item.get("seasons", [])

            season_summary = []
            for s in sorted(seasons, key=lambda x: x.get("number", 0)):
                num = s.get("number", "?")
                ep_count = len(s.get("episodes", []))
                season_summary.append(f"S{num}: {ep_count} episodes")

            content = f'Show: "{title}"' + (f" ({year})" if year else "")
            content += f"\nTotal plays: {plays}"
            if last_watched:
                content += f"\nLast watched: {last_watched}"
            if season_summary:
                content += f"\nSeasons watched: {', '.join(season_summary)}"

            ids = show.get("ids", {})
            docs.append(Document(
                id=f"trakt_show_{ids.get('trakt', _h(title))}",
                content=content,
                source="trakt",
                title=f"{title}" + (f" ({year})" if year else ""),
                metadata={"type": "show", "year": str(year), "plays": plays, "seasons": len(seasons), "date": last_watched, "status": "consumed"},
            ))
        return docs

    def _ingest_ratings(self) -> list[Document]:
        """Fetch user's rated movies and shows — these are things they explicitly liked."""
        docs = []
        for media_type in ("movies", "shows"):
            try:
                items = self._get(f"/users/{self.username}/ratings/{media_type}")
                for item in items:
                    obj = item.get("movie") or item.get("show") or {}
                    title = obj.get("title", "Unknown")
                    year = obj.get("year", "")
                    rating = item.get("rating", 0)
                    rated_at = (item.get("rated_at") or "")[:10]
                    ids = obj.get("ids", {})
                    kind = "Movie" if media_type == "movies" else "Show"
                    content = (
                        f'{kind}: "{title}"' + (f" ({year})" if year else "") +
                        f"\nMy rating: {rating}/10" +
                        (f"\nRated: {rated_at}" if rated_at else "")
                    )
                    doc_id = f"trakt_rating_{media_type}_{ids.get('trakt', _h(title))}"
                    docs.append(Document(
                        id=doc_id,
                        content=content,
                        source="trakt",
                        title=f"{title}" + (f" ({year})" if year else ""),
                        metadata={
                            "type": f"rated_{media_type[:-1]}",
                            "year": str(year),
                            "rating": rating,
                            "date": rated_at,
                            "status": "consumed",
                        },
                    ))
                    # Enrich highly-rated content with Wikipedia
                    if rating and rating >= 8:
                        wiki_doc = enrich_book(title, "")
                        if wiki_doc:
                            docs.append(wiki_doc)
            except Exception:
                pass
        return docs

    def _ingest_watchlist(self) -> list[Document]:
        """Fetch user's Trakt watchlist — saved but not yet watched."""
        docs = []
        try:
            items = self._get(f"/users/{self.username}/watchlist")
            for item in items:
                obj = item.get("movie") or item.get("show") or item.get("episode") or {}
                title = obj.get("title", "Unknown")
                year = obj.get("year", "")
                media_type = item.get("type", "item")
                listed_at = (item.get("listed_at") or "")[:10]
                ids = obj.get("ids", {})
                content = (
                    f'{media_type.title()}: "{title}"' + (f" ({year})" if year else "") +
                    f"\nStatus: On watchlist (not yet watched)" +
                    (f"\nAdded: {listed_at}" if listed_at else "")
                )
                doc_id = f"trakt_watchlist_{ids.get('trakt', _h(title))}"
                docs.append(Document(
                    id=doc_id,
                    content=content,
                    source="trakt",
                    title=f"{title}" + (f" ({year})" if year else ""),
                    metadata={
                        "type": f"watchlist_{media_type}",
                        "year": str(year),
                        "date": listed_at,
                        "status": "saved",
                    },
                ))
                # Enrich watchlist items with Wikipedia so Jimmy knows what they are
                wiki_doc = enrich_book(title, "")
                if wiki_doc:
                    docs.append(wiki_doc)
        except Exception:
            pass
        return docs
