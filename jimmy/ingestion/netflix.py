"""Netflix viewing history ingester.

Netflix lets you export your viewing history as a CSV:
  Account → Security & Privacy → Download personal info
  OR: netflix.com/viewingactivity → Download All

The CSV format is:
  Title,Date
  "Stranger Things: Season 2: \"Chapter One: MADMAX\"","01/01/2024"

Parses the CSV, groups by show/movie, and creates Documents.
Also supports the newer format with additional columns.
"""

import csv
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from .base import Document, _h
from .enrich_book import enrich_book


def _parse_date(date_str: str) -> str:
    """Parse Netflix date formats to YYYY-MM-DD."""
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return date_str[:10]


def _extract_show_title(full_title: str) -> tuple[str, str | None]:
    """Split 'Show: Season X: Episode Title' into (show_name, episode_info).
    Returns (full_title, None) for movies.
    """
    # Netflix format: "Show Title: Season N: Episode Title" or "Show Title: Episode Title"
    parts = [p.strip() for p in full_title.split(":") if p.strip()]
    if len(parts) >= 2:
        # Heuristic: if second part starts with "Season" or "Part" or "Volume", it's a show
        if re.match(r"^(Season|Part|Volume|Series|Book|Chapter)\s+\d", parts[1], re.IGNORECASE):
            return parts[0], ": ".join(parts[1:])
        # If there are 3+ parts, first is likely the show
        if len(parts) >= 3:
            return parts[0], ": ".join(parts[1:])
    return full_title, None


class NetflixIngester:
    def ingest(self, csv_path: str, enrich: bool = True) -> list[Document]:
        """Parse Netflix viewing history CSV and return Documents.

        Download from: netflix.com/viewingactivity (click 'Download All')
        """
        p = Path(csv_path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Netflix history not found: {csv_path}")

        # Group episodes by show, movies separately
        shows: dict[str, list[dict]] = defaultdict(list)
        movies: list[dict] = []

        with open(p, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            # Handle both column name variants
            for row in reader:
                title_key = next((k for k in row if "title" in k.lower()), None)
                date_key = next((k for k in row if "date" in k.lower()), None)
                if not title_key:
                    continue
                full_title = (row.get(title_key) or "").strip()
                date_raw = (row.get(date_key) or "") if date_key else ""
                date_str = _parse_date(date_raw) if date_raw else ""
                if not full_title:
                    continue

                show_name, episode = _extract_show_title(full_title)
                if episode:
                    shows[show_name].append({"episode": episode, "date": date_str})
                else:
                    movies.append({"title": full_title, "date": date_str})

        docs = []

        # One document per show
        for show_name, episodes in shows.items():
            episodes_sorted = sorted(episodes, key=lambda x: x["date"], reverse=True)
            most_recent = episodes_sorted[0]["date"] if episodes_sorted else ""
            earliest = episodes_sorted[-1]["date"] if episodes_sorted else ""
            sample = episodes_sorted[:10]
            ep_lines = [f"  - {e['episode']}" + (f" ({e['date']})" if e['date'] else "") for e in sample]

            content = (
                f'Netflix Show: "{show_name}"\n'
                f"Episodes watched: {len(episodes)}\n"
                + (f"First watched: {earliest}\n" if earliest else "")
                + (f"Last watched: {most_recent}\n" if most_recent else "")
                + (f"\nRecent episodes:\n" + "\n".join(ep_lines) if ep_lines else "")
            )
            docs.append(Document(
                id=f"netflix_show_{_h(show_name)}",
                content=content,
                source="trakt",   # use trakt source weight for streaming content
                title=f"Netflix: {show_name}",
                metadata={
                    "type": "show",
                    "date": most_recent,
                    "status": "consumed",
                    "service": "netflix",
                },
            ))
            # Enrich binge-watched shows with Wikipedia
            if enrich and len(episodes) >= 3:
                wiki_doc = enrich_book(show_name, "")
                if wiki_doc:
                    docs.append(wiki_doc)

        # One document per movie
        for movie in movies:
            title = movie["title"]
            date_str = movie["date"]
            content = (
                f'Netflix Movie: "{title}"\n'
                + (f"Watched: {date_str}" if date_str else "")
            )
            docs.append(Document(
                id=f"netflix_movie_{_h(title)}",
                content=content,
                source="trakt",
                title=f"Netflix: {title}",
                metadata={
                    "type": "movie",
                    "date": date_str,
                    "status": "consumed",
                    "service": "netflix",
                },
            ))
            if enrich:
                wiki_doc = enrich_book(title, "")
                if wiki_doc:
                    docs.append(wiki_doc)

        print(f"  Netflix: {len(shows)} shows, {len(movies)} movies from history")
        return docs
