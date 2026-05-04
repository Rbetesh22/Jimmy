"""Apple Photos ingester — reads Photos.sqlite directly (no AppleScript, no permissions popup)."""
import sqlite3
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from .base import Document

PHOTOS_DB = Path.home() / "Pictures/Photos Library.photoslibrary/database/Photos.sqlite"
ORIGINALS_DIR = Path.home() / "Pictures/Photos Library.photoslibrary/originals"
APPLE_EPOCH = datetime(2001, 1, 1)  # Core Data epoch offset


def _apple_ts_to_date(ts: float) -> str:
    """Convert Apple Core Data timestamp (seconds since 2001-01-01) to YYYY-MM-DD."""
    return (APPLE_EPOCH + timedelta(seconds=ts)).date().isoformat()


class PhotosIngester:
    def ingest(
        self,
        ai_describe: bool = False,
        limit: int | None = None,
        since: str | None = None,
        include_videos: bool = True,
    ) -> list[Document]:
        if not PHOTOS_DB.exists():
            raise FileNotFoundError(f"Photos library not found at {PHOTOS_DB}")

        # Copy DB to temp file — Photos keeps a write lock on the live DB
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            shutil.copy2(PHOTOS_DB, tmp.name)
            tmp_path = tmp.name

        docs: list[Document] = []
        try:
            con = sqlite3.connect(tmp_path)
            con.row_factory = sqlite3.Row
            cur = con.cursor()

            cur.execute("""
                SELECT
                    a.Z_PK, a.ZDIRECTORY, a.ZFILENAME, a.ZKIND,
                    a.ZDATECREATED, a.ZLATITUDE, a.ZLONGITUDE,
                    a.ZFAVORITE, a.ZDURATION, a.ZHIDDEN,
                    attr.ZACCESSIBILITYDESCRIPTION
                FROM ZASSET a
                LEFT JOIN ZADDITIONALASSETATTRIBUTES attr ON attr.ZASSET = a.Z_PK
                WHERE a.ZHIDDEN = 0
                ORDER BY a.ZDATECREATED DESC
            """)
            rows = cur.fetchall()
            con.close()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        # Apply date filter
        since_cutoff = since or ""

        processed = 0
        for row in rows:
            if limit is not None and processed >= limit:
                break

            kind = row["ZKIND"]          # 0 = photo, 1 = video
            is_video = kind == 1

            if is_video and not include_videos:
                continue

            ts = row["ZDATECREATED"]
            if not ts:
                continue
            date_str = _apple_ts_to_date(float(ts))

            if since_cutoff and date_str < since_cutoff:
                continue

            pk = row["Z_PK"]
            directory = row["ZDIRECTORY"] or ""
            filename = row["ZFILENAME"] or ""
            file_path = ORIGINALS_DIR / directory / filename

            on_device_desc = row["ZACCESSIBILITYDESCRIPTION"] or ""

            if is_video:
                content = self._transcribe_video(file_path) if file_path.exists() else ""
                if not content or len(content) < 50:
                    continue
                source = "videos"
                title = f"Video — {date_str}"
            else:
                if on_device_desc and len(on_device_desc) > 20:
                    content = on_device_desc
                elif ai_describe and file_path.exists():
                    try:
                        content = self._describe_photo(file_path)
                    except Exception:
                        continue
                else:
                    continue  # no description, no AI — skip
                source = "photos"
                title = f"Photo — {date_str}"

            lat = row["ZLATITUDE"]
            lon = row["ZLONGITUDE"]
            metadata: dict = {
                "date": date_str,
                "filename": filename,
                "kind": kind,
                "is_favorite": bool(row["ZFAVORITE"]),
            }
            if lat is not None and lat != -180.0:
                metadata["latitude"] = lat
                metadata["longitude"] = lon
            if row["ZDURATION"]:
                metadata["duration"] = row["ZDURATION"]

            docs.append(Document(
                id=f"photos_{pk}",
                content=content,
                source=source,
                title=title,
                metadata=metadata,
            ))
            processed += 1

        return docs

    def _describe_photo(self, file_path: Path) -> str:
        import base64
        import anthropic
        with open(file_path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        ext = file_path.suffix.lower().lstrip(".")
        media_type = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "heic": "image/jpeg",
        }.get(ext, "image/jpeg")
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                    {"type": "text", "text": (
                        "Describe this photo in 2-3 sentences for a personal photo archive. "
                        "Focus on who/what is in the scene, where it appears to be, and any notable context."
                    )},
                ],
            }],
        )
        return msg.content[0].text

    def _transcribe_video(self, file_path: Path) -> str:
        import subprocess
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ImportError("Install faster-whisper to transcribe videos: pip install faster-whisper")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_wav = tmp.name

        try:
            result = subprocess.run(
                ["ffmpeg", "-i", str(file_path), "-vn", "-ar", "16000", "-ac", "1", "-f", "wav", tmp_wav, "-y", "-loglevel", "error"],
                capture_output=True,
            )
            if result.returncode != 0:
                return ""
            model = WhisperModel("base", device="cpu")
            segments, _ = model.transcribe(tmp_wav)
            return " ".join(seg.text for seg in segments).strip()
        finally:
            Path(tmp_wav).unlink(missing_ok=True)
