import hashlib
import re
import subprocess
from .base import Document

BATCH_SIZE = 50


def _run_script(script: str, timeout: int = 120) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def _fetch_batch(start: int, end: int) -> str:
    """Fetch notes start..end (1-indexed, inclusive) as delimited text."""
    script = f'''tell application "Notes"
set out to ""
repeat with i from {start} to {end}
try
set n to note i
set modDate to modification date of n
set y to year of modDate as text
set m to (month of modDate as integer) as text
set d to day of modDate as text
set dateStr to y & "-" & m & "-" & d
set out to out & "===NOTE===
" & (name of n) & "
DATE:" & dateStr & "
" & (plaintext of n) & "
"
end try
end repeat
return out
end tell'''
    return _run_script(script, timeout=120)


class AppleNotesIngester:
    def ingest(self, on_progress=None) -> list[Document]:
        # Get total count first
        count = int(_run_script('tell application "Notes" to return count of notes'))

        docs = []
        for start in range(1, count + 1, BATCH_SIZE):
            end = min(start + BATCH_SIZE - 1, count)
            if on_progress:
                on_progress(start, end, count)
            try:
                raw = _fetch_batch(start, end)
                docs.extend(_parse(raw))
            except Exception as e:
                print(f"  batch {start}-{end} failed: {e}")

        return docs


def _parse(raw: str) -> list[Document]:
    docs = []
    for entry in raw.split("===NOTE==="):
        lines = entry.strip().split("\n")
        if len(lines) < 2:
            continue
        title = lines[0].strip()
        date = ""
        content_start = 1
        if len(lines) > 1 and lines[1].startswith("DATE:"):
            raw_date = lines[1][5:].strip()  # e.g. "2025-1-5"
            # Normalize to YYYY-MM-DD
            try:
                parts = raw_date.split("-")
                date = f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
            except Exception:
                date = raw_date
            content_start = 2
        content = "\n".join(lines[content_start:]).strip()
        if len(content) < 30:
            continue
        stable_id = hashlib.md5((title + content[:50]).encode(), usedforsecurity=False).hexdigest()[:16]
        docs.append(Document(
            id=f"apple_notes_{stable_id}",
            content=f"{title}\n\n{content}",
            source="apple_notes",
            title=f"Note: {title}",
            metadata={"type": "note", "date": date},
        ))
    return docs
