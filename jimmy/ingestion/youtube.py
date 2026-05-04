import re
import httpx
from .base import Document, _h


def _extract_video_id(url: str) -> str | None:
    patterns = [
        r"youtube\.com/watch\?v=([^&]+)",
        r"youtu\.be/([^?]+)",
        r"youtube\.com/embed/([^?]+)",
        r"youtube\.com/shorts/([^?/]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _get_title(video_id: str) -> str:
    """Fetch the video title from the YouTube page."""
    try:
        r = httpx.get(
            f"https://www.youtube.com/watch?v={video_id}",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        )
        # Try og:title first (more reliable)
        m = re.search(r'<meta property="og:title" content="([^"]+)"', r.text)
        if m:
            return m.group(1)
        # Fallback to <title> tag
        m = re.search(r"<title>(.*?) - YouTube</title>", r.text)
        return m.group(1) if m else video_id
    except Exception:
        return video_id


def _chunk_transcript_by_time(transcript: list, chunk_seconds: int = 120) -> list[dict]:
    """
    Group transcript entries into time-based segments (~2 minutes each).
    Returns list of dicts with 'text', 'start_time', 'end_time'.
    """
    if not transcript:
        return []

    segments = []
    current_texts = []
    current_start = transcript[0].start if transcript else 0
    current_end = current_start

    for entry in transcript:
        # Start a new segment if we've exceeded the time window
        if entry.start - current_start >= chunk_seconds and current_texts:
            segments.append({
                "text": " ".join(current_texts),
                "start_time": current_start,
                "end_time": current_end,
                "start_fmt": _fmt_time(current_start),
            })
            current_texts = []
            current_start = entry.start

        current_texts.append(entry.text.strip())
        current_end = entry.start + getattr(entry, "duration", 0)

    # Flush final segment
    if current_texts:
        segments.append({
            "text": " ".join(current_texts),
            "start_time": current_start,
            "end_time": current_end,
            "start_fmt": _fmt_time(current_start),
        })

    return segments


def _fmt_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS or MM:SS."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


class YouTubeIngester:
    def ingest(self, url: str) -> list[Document]:
        from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled

        video_id = _extract_video_id(url)
        if not video_id:
            raise ValueError(f"Could not extract video ID from: {url}")

        # Fetch transcript
        try:
            api = YouTubeTranscriptApi()
            transcript_list = api.fetch(video_id)
            transcript = list(transcript_list)
        except TranscriptsDisabled:
            raise ValueError(
                f"Transcripts are disabled for this video: {url}\n"
                "The video owner has turned off captions/subtitles."
            )
        except NoTranscriptFound:
            raise ValueError(
                f"No transcript found for video: {url}\n"
                "This video may not have captions available."
            )
        except Exception as e:
            raise ValueError(f"Failed to fetch transcript for {url}: {e}")

        if not transcript:
            raise ValueError(f"Empty transcript for video: {url}")

        title = _get_title(video_id)
        full_title = f"YouTube: {title}"

        # Build time-segmented chunks — each is a ~2-minute segment
        segments = _chunk_transcript_by_time(transcript, chunk_seconds=120)

        if len(segments) <= 1:
            # Short video: single document
            full_text = " ".join(entry.text for entry in transcript)
            return [Document(
                id=f"youtube_{video_id}",
                content=f"[Video: {title}]\n[URL: {url}]\n\n{full_text}",
                source="youtube",
                title=full_title,
                metadata={
                    "type": "video_transcript",
                    "url": url,
                    "video_id": video_id,
                    "tag": "video",
                },
            )]

        # Multi-segment: create one Document per segment for finer-grained retrieval
        docs = []
        for i, seg in enumerate(segments):
            seg_text = f"[Video: {title}] [Time: {seg['start_fmt']}]\n[URL: {url}?t={int(seg['start_time'])}]\n\n{seg['text']}"
            docs.append(Document(
                id=f"youtube_{video_id}_s{i}",
                content=seg_text,
                source="youtube",
                title=f"{full_title} [{seg['start_fmt']}]",
                metadata={
                    "type": "video_transcript",
                    "url": url,
                    "video_id": video_id,
                    "segment": i,
                    "start_time": seg["start_time"],
                    "start_fmt": seg["start_fmt"],
                    "tag": "video",
                },
            ))

        return docs
