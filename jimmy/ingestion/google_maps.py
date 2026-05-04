"""Google Takeout — Maps location history & saved places ingester."""
import json
from pathlib import Path
from .base import Document, _h


class GoogleMapsIngester:
    def ingest(self, takeout_path: str) -> list[Document]:
        """Parse Google Maps data from Takeout.

        takeout_path: path to Takeout/ folder or specific JSON file
        """
        p = Path(takeout_path)
        docs = []

        # Saved places
        saved_candidates = [
            p / "Maps (your places)" / "Saved Places.json",
            p / "Maps" / "Saved Places.json",
            p / "Saved Places.json",
        ]
        for candidate in saved_candidates:
            if candidate.is_file():
                docs.extend(self._parse_saved_places(candidate))
                break

        # Location history (Records.json — new format)
        records_candidates = [
            p / "Location History (Timeline)" / "Records.json",
            p / "Location History" / "Records.json",
            p / "Records.json",
        ]
        for candidate in records_candidates:
            if candidate.is_file():
                docs.extend(self._parse_location_records(candidate))
                break

        return docs

    def _parse_saved_places(self, path: Path) -> list[Document]:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        features = data.get("features", data) if isinstance(data, dict) else data
        if isinstance(features, dict):
            features = features.get("features", [])

        docs = []
        for feature in features:
            props = feature.get("properties", {})
            title = props.get("Title", props.get("name", ""))
            address = props.get("Address", props.get("address", ""))
            url = props.get("Google Maps URL", props.get("url", ""))
            date = props.get("Published", props.get("date", ""))[:10] if props.get("Published") else ""

            if not title:
                location = props.get("location", {})
                title = location.get("name", "")
                address = location.get("address", address)

            if not title or len(title) < 2:
                continue

            content = f"Saved place: {title}"
            if address:
                content += f"\nAddress: {address}"
            if date:
                content += f"\nSaved: {date}"

            docs.append(Document(
                id=f"maps_saved_{_h(title + address)}",
                content=content,
                source="google_maps",
                title=f"Saved: {title[:80]}",
                metadata={"type": "saved_place", "address": address, "date": date, "url": url},
            ))
        return docs

    def _parse_location_records(self, path: Path, max_records: int = 500) -> list[Document]:
        """Parse significant location visits (not raw GPS points)."""
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        locations = data.get("locations", data) if isinstance(data, dict) else data
        if isinstance(locations, dict):
            locations = locations.get("locations", [])

        # Group by date, only keep records with semantic location info
        by_date: dict[str, list[str]] = {}
        count = 0
        for record in locations:
            if count >= max_records:
                break
            # New format: placeVisit, activitySegment
            place = record.get("placeVisit", {})
            location = place.get("location", {})
            name = location.get("name", "")
            address = location.get("address", "")

            if not name:
                # Try old format
                semantic = record.get("semanticType", "")
                if semantic:
                    name = semantic.replace("TYPE_", "").replace("_", " ").title()
                else:
                    continue

            timestamp = record.get("timestamp", place.get("duration", {}).get("startTimestamp", ""))
            date = timestamp[:10] if len(timestamp) >= 10 else ""
            if not date:
                continue

            entry = name
            if address:
                entry += f" ({address})"

            by_date.setdefault(date, []).append(entry)
            count += 1

        docs = []
        for date, places in sorted(by_date.items(), reverse=True):
            content = f"Places visited on {date}:\n" + "\n".join(f"- {p}" for p in places)
            docs.append(Document(
                id=f"maps_visit_{_h(date + str(len(places)))}",
                content=content,
                source="google_maps",
                title=f"Locations: {date}",
                metadata={"type": "location_history", "date": date},
            ))
        return docs
