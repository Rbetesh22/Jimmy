import json
import os
import zipfile
import tempfile
from pathlib import Path
from datetime import datetime
from .base import Document, _h


def _parse_posts_json(json_path: str) -> list[Document]:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Instagram posts JSON can be a list or have a 'media' key
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("media", [])
    else:
        return []

    docs = []
    for item in items:
        # Each item may have a list of media sub-items
        media_list = item if isinstance(item, list) else [item]
        for media in media_list:
            caption = ""
            if isinstance(media.get("title"), str):
                caption = media["title"].strip()
            elif isinstance(media.get("media_metadata"), dict):
                caption = media["media_metadata"].get("photo_metadata", {}).get("exif_data", [{}])[0].get("source", "")

            if not caption or len(caption) < 10:
                continue

            ts = media.get("creation_timestamp", 0)
            date_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""
            uri = media.get("uri", "")

            content = caption
            if date_str:
                content += f"\n\nPosted: {date_str}"
            if uri:
                content += f"\nMedia: {uri}"

            doc_id = f"instagram_{_h(caption + str(ts))}"
            docs.append(Document(
                id=doc_id,
                content=content,
                source="instagram",
                title=f"Instagram: {caption[:80]}{'...' if len(caption) > 80 else ''}",
                metadata={"type": "post", "date": date_str},
            ))
    return docs


class InstagramIngester:
    def ingest(self, path: str) -> list[Document]:
        """Parse Instagram data export (folder or ZIP)."""
        p = Path(path)
        tmp_dir = None

        if p.suffix == ".zip":
            tmp_dir = tempfile.mkdtemp()
            with zipfile.ZipFile(path, "r") as z:
                z.extractall(tmp_dir)
            target = tmp_dir
        else:
            target = path

        docs = []
        try:
            # Walk looking for post JSON files
            for root, _, files in os.walk(target):
                for fname in files:
                    if fname.endswith(".json") and ("post" in fname.lower() or "media" in fname.lower()):
                        try:
                            docs.extend(_parse_posts_json(os.path.join(root, fname)))
                        except Exception:
                            pass
        finally:
            if tmp_dir:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)

        return docs
