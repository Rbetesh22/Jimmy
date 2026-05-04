"""Wikipedia-based book/article enrichment.

For any book or article in the knowledge base, fetches a Wikipedia summary
so Jimmy has context about the content even if the user hasn't read it yet.
"""

import httpx
from urllib.parse import quote
from .base import Document, _h


def _fetch_wikipedia(title: str) -> dict | None:
    """Query Wikipedia REST API for a page summary. Returns API JSON or None."""
    # Strip edition/subtitle noise: "Thinking, Fast and Slow (2011)" → "Thinking, Fast and Slow"
    clean = title.split("(")[0].split(":")[0].strip()
    if len(clean) < 3:
        return None
    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(clean)}"
        r = httpx.get(url, timeout=10, follow_redirects=True,
                      headers={"User-Agent": "JimmyBot/1.0"})
        if r.status_code == 200:
            data = r.json()
            # Skip disambiguation pages and very short extracts
            if data.get("type") == "disambiguation":
                return None
            if len(data.get("extract", "")) < 80:
                return None
            return data
    except Exception:
        pass
    return None


def enrich_book(
    title: str,
    author: str = "",
    course_name: str = "",
    source_id: str = "",
) -> Document | None:
    """Return a Wikipedia summary Document for a book, or None if not found."""
    query = f"{title} {author}".strip() if author else title
    data = _fetch_wikipedia(query) or _fetch_wikipedia(title)
    if not data:
        return None

    wiki_title = data.get("title", title)
    extract = data.get("extract", "")
    description = data.get("description", "")
    wiki_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")

    lines = [f'"{wiki_title}"']
    if description:
        lines.append(description)
    lines.append("")
    lines.append(extract)
    if author:
        lines.append(f"\nAuthor: {author}")
    if course_name:
        lines.append(f"Course: {course_name}")

    return Document(
        id=f"wiki_book_{_h(title + author)}",
        content="\n".join(lines),
        source="web",
        title=f"Wikipedia: {wiki_title}",
        metadata={
            "type": "book_summary",
            "book_title": title,
            "author": author,
            "url": wiki_url,
            "status": "reference",
        },
    )
