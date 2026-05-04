"""Audio ingester — transcribes voice memos and audio files.

Priority:
  1. OpenAI Whisper API (if OPENAI_API_KEY is set) — cloud, no GPU needed.
  2. faster-whisper (local) — if installed and no API key available.
"""
import os
from datetime import datetime
from pathlib import Path

from .base import Document, _h

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aiff", ".aif", ".caf", ".webm", ".mp4", ".ogg"}


def _transcribe_with_openai(file_path: Path) -> str:
    """Transcribe a single file via OpenAI Whisper API."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    with open(file_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="text",
        )
    return str(result).strip()


def _transcribe_with_faster_whisper(file_path: Path) -> str:
    """Transcribe a single file using local faster-whisper."""
    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu")
    segments, _ = model.transcribe(str(file_path))
    return " ".join(seg.text for seg in segments).strip()


def _transcribe(file_path: Path) -> str:
    """Transcribe using OpenAI API first, then fall back to faster-whisper."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if api_key:
        return _transcribe_with_openai(file_path)
    try:
        return _transcribe_with_faster_whisper(file_path)
    except ImportError:
        raise ImportError(
            "No transcription backend available. Either set OPENAI_API_KEY in .env "
            "or install faster-whisper: pip install faster-whisper"
        )


class AudioIngester:
    def ingest(self, directory: str | Path, source: str = "voice_memo") -> list[Document]:
        directory = Path(directory).expanduser()
        if not directory.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")

        docs: list[Document] = []

        audio_files = sorted(
            (p for p in directory.rglob("*") if p.suffix.lower() in AUDIO_EXTS),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for file in audio_files:
            print(f"  {file.name}...")
            try:
                text = _transcribe(file)
            except Exception as e:
                print(f"    skipping {file.name}: {e}")
                continue

            if len(text) < 20:
                continue  # silence or noise

            date_str = datetime.fromtimestamp(file.stat().st_mtime).date().isoformat()
            doc_id = f"audio_{_h(str(file.resolve()))}"

            docs.append(Document(
                id=doc_id,
                content=text,
                source=source,
                title=file.stem,
                metadata={"date": date_str, "filename": file.name},
            ))

        return docs
