import csv
import zipfile
import tempfile
import os
from pathlib import Path
from .base import Document, _h


def _read_csv_from_dir(folder: str, filename: str) -> list[dict]:
    path = os.path.join(folder, filename)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


class LetterboxdIngester:
    def ingest(self, path: str) -> list[Document]:
        """Parse Letterboxd data export (ZIP or folder)."""
        p = Path(path)
        tmp_dir = None

        if p.suffix == ".zip":
            tmp_dir = tempfile.mkdtemp()
            with zipfile.ZipFile(path, "r") as z:
                z.extractall(tmp_dir)
            folder = tmp_dir
        else:
            folder = path

        try:
            return self._parse(folder)
        finally:
            if tmp_dir:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def _parse(self, folder: str) -> list[Document]:
        # Build film data from diary (most complete — has watch date + rating)
        diary = _read_csv_from_dir(folder, "diary.csv")
        ratings = _read_csv_from_dir(folder, "ratings.csv")
        reviews = _read_csv_from_dir(folder, "reviews.csv")
        watched = _read_csv_from_dir(folder, "watched.csv")

        # Index reviews by film name
        review_by_film: dict[str, str] = {}
        for row in reviews:
            name = row.get("Name", "").strip()
            review_text = row.get("Review", "").strip()
            if name and review_text:
                review_by_film[name] = review_text

        # Index ratings by film name
        rating_by_film: dict[str, str] = {}
        for row in ratings:
            name = row.get("Name", "").strip()
            rating = row.get("Rating", "").strip()
            if name and rating:
                rating_by_film[name] = rating

        # Primary source: diary entries (one per watch)
        seen_films: set[str] = set()
        docs = []

        for row in diary:
            name = row.get("Name", "").strip()
            if not name:
                continue
            year = row.get("Year", "").strip()
            watch_date = row.get("Watched Date", row.get("Date", "")).strip()
            rating = row.get("Rating", rating_by_film.get(name, "")).strip()
            rewatch = row.get("Rewatch", "").strip().lower() == "yes"
            review = review_by_film.get(name, "")

            film_key = name + year
            lines = [f'"{name}"' + (f" ({year})" if year else "")]
            if watch_date:
                label = "Rewatched" if rewatch else "Watched"
                lines.append(f"{label}: {watch_date}")
            if rating:
                lines.append(f"Rating: {rating}/5")
            if review:
                lines.append(f"\nReview:\n{review}")

            content = "\n".join(lines)
            doc_id = f"letterboxd_{_h(film_key + watch_date)}"
            docs.append(Document(
                id=doc_id,
                content=content,
                source="letterboxd",
                title=f"{name}" + (f" ({year})" if year else ""),
                metadata={
                    "type": "film",
                    "rating": rating,
                    "watch_date": watch_date,
                    "rewatch": rewatch,
                },
            ))
            seen_films.add(film_key)

        # Add any rated/reviewed films not in diary
        for row in ratings:
            name = row.get("Name", "").strip()
            year = row.get("Year", "").strip()
            if not name or (name + year) in seen_films:
                continue
            rating = row.get("Rating", "").strip()
            review = review_by_film.get(name, "")
            date = row.get("Date", "").strip()

            lines = [f'"{name}"' + (f" ({year})" if year else "")]
            if date:
                lines.append(f"Logged: {date}")
            if rating:
                lines.append(f"Rating: {rating}/5")
            if review:
                lines.append(f"\nReview:\n{review}")

            content = "\n".join(lines)
            docs.append(Document(
                id=f"letterboxd_{_h(name + year + rating)}",
                content=content,
                source="letterboxd",
                title=f"{name}" + (f" ({year})" if year else ""),
                metadata={"type": "film", "rating": rating},
            ))
            seen_films.add(name + year)

        return docs
