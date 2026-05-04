import re
import trafilatura
import trafilatura.settings
from .base import Document, _h


# Paywall indicators — sites/patterns that commonly block content
_PAYWALL_PATTERNS = [
    r"subscribe to (continue|read|access)",
    r"this (article|content|story) is (for|available to) (subscriber|member|paid)",
    r"(create|sign up for) a free account to (read|continue|access)",
    r"you('ve| have) reached your (free article|monthly article|article) limit",
    r"(log|sign) in to (read|continue|access|view) (this|the full)",
    r"unlock (this|full) (article|story|content)",
    r"get unlimited access",
    r"already a subscriber\? sign in",
]
_PAYWALL_RE = re.compile("|".join(_PAYWALL_PATTERNS), re.IGNORECASE)

# Auto-tagging rules: (url_pattern, tag)
_URL_TAGS = [
    (r"wikipedia\.org", "reference"),
    (r"arxiv\.org", "reference"),
    (r"pubmed\.ncbi|ncbi\.nlm\.nih", "reference"),
    (r"youtube\.com|youtu\.be", "video"),
    (r"twitter\.com|x\.com", "social"),
    (r"github\.com|gitlab\.com", "code"),
]


def _auto_tag(url: str) -> str:
    """Return a tag string based on the URL domain."""
    for pattern, tag in _URL_TAGS:
        if re.search(pattern, url, re.IGNORECASE):
            return tag
    return "web"


def _detect_paywall(html: str, content: str, url: str) -> bool:
    """Return True if the page appears to be paywalled/blocked."""
    # Very short content after extraction is suspicious
    if content and len(content.strip()) > 300:
        return False

    # Check for paywall signals in the raw HTML
    sample = html[:8000].lower() if html else ""
    if _PAYWALL_RE.search(sample):
        return True

    # Specific paywall domains
    paywall_domains = [
        "wsj.com", "nytimes.com", "ft.com", "bloomberg.com",
        "thetimes.co.uk", "telegraph.co.uk", "economist.com",
        "newyorker.com", "theatlantic.com", "wired.com",
    ]
    return any(d in url for d in paywall_domains) and (not content or len(content) < 200)


class WebIngester:
    def ingest(self, url: str) -> list[Document]:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            raise ValueError(f"Could not fetch URL (network error or blocked): {url}")

        # Extract content with trafilatura (readability-style main content extraction)
        content = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            favor_recall=True,        # prefer recall over precision for article bodies
            include_formatting=False,  # plain text, no markdown noise
        )

        # Paywall detection
        if _detect_paywall(downloaded, content or "", url):
            raise ValueError(
                f"Content appears to be paywalled or access-restricted at: {url}\n"
                "Try: copy the article text and use /ingest/text instead."
            )

        if not content or len(content) < 100:
            raise ValueError(
                f"Could not extract readable content from: {url}\n"
                "The page may be JavaScript-rendered, empty, or require login."
            )

        # Get metadata
        meta = trafilatura.extract_metadata(downloaded)
        title = (meta.title if meta and meta.title else None) or url
        author = meta.author if meta and meta.author else None
        date = meta.date if meta and meta.date else None

        # Clean up title — strip trailing " - Site Name" patterns for cleanliness
        if title and " - " in title and len(title) > 80:
            # Keep it if it's a meaningful double-part title; otherwise trim
            parts = title.rsplit(" - ", 1)
            if len(parts[0]) > 20:
                title = parts[0].strip()

        auto_tag = _auto_tag(url)

        return [Document(
            id=f"web_{_h(url)}",
            content=content,
            source="web",
            title=title,
            metadata={
                "type": "article",
                "url": url,
                "author": author or "",
                "date": date or "",
                "tag": auto_tag,
            },
        )]
