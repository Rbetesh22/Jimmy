"""Course website crawler.

Crawls external course websites (e.g. columbia-os.github.io) linked from Canvas
pages to discover and ingest slides, notes, and recordings.

Strategy (bounded crawl):
  Level 0: root URL — extract materials + nav links
  Level 1: follow nav links on the same domain — extract materials
  Max pages visited: 40
  Only follows links staying on the same domain as the root.

Material types handled:
  PDF / PPTX / Google Slides / Google Docs → downloaded and text-extracted
  YouTube / Zoom / Panopto recordings      → stored as a stub doc with URL
"""

import re
import tempfile
import httpx
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

from .base import Document, _h
from .file import FileIngester

# Domains we should NOT crawl as "course sites" — they're known services
_SKIP_DOMAINS = {
    "google.com", "youtube.com", "youtu.be", "zoom.us",
    "dropbox.com", "box.com", "drive.google.com",
    "piazza.com", "gradescope.com", "edstem.org", "ed.link",
    "overleaf.com", "sharepoint.com", "office.com",
    "twitter.com", "x.com", "linkedin.com", "instagram.com",
    "facebook.com", "slack.com", "discord.com",
    "amazon.com", "github.com",   # github.com = profiles/repos; github.io = course sites
    "wikipedia.org", "arxiv.org",
}

# URL path/text patterns that suggest a nav link is a schedule/lecture page
_NAV_KEYWORDS = re.compile(
    r"schedule|lecture|lec\d|class|slide|note|material|week|lab|hw|handout|calendar|syllabus",
    re.IGNORECASE,
)

# Link text / URL patterns for recording services
_RECORDING_RE = re.compile(
    r"(youtube\.com|youtu\.be|zoom\.us/rec|panopto|mediasite|kaltura|vimeo\.com)",
    re.IGNORECASE,
)

# Supported downloadable extensions
_SLIDE_EXTS = {".pdf", ".pptx", ".docx"}


def _root_domain(url: str) -> str:
    """Return the netloc (host) of a URL."""
    return urlparse(url).netloc.lower()


def _resolve(base: str, href: str) -> str | None:
    """Resolve href relative to base, return None if not http(s)."""
    try:
        resolved = urljoin(base, href.strip())
        if resolved.startswith(("http://", "https://")):
            return resolved
    except Exception:
        pass
    return None


def _is_service_domain(url: str) -> bool:
    domain = _root_domain(url)
    # Exact match or subdomain match
    return any(domain == d or domain.endswith("." + d) for d in _SKIP_DOMAINS)


def _extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Return list of (absolute_url, link_text) from <a href> tags."""
    raw = re.findall(
        r'<a[^>]+href=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>',
        html, re.IGNORECASE | re.DOTALL,
    )
    result = []
    for href, inner in raw:
        url = _resolve(base_url, href)
        if url:
            text = re.sub(r"<[^>]+>", "", inner).strip()
            result.append((url, text))
    return result


def _classify_link(url: str, text: str) -> str:
    """Return 'material', 'recording', 'nav', or 'skip'."""
    low = url.lower()
    path_end = PurePosixPath(urlparse(url).path).suffix.lower()

    # Google Slides / Docs
    if "docs.google.com/presentation" in url:
        return "material"
    if "docs.google.com/document" in url:
        return "material"

    # Downloadable files
    if path_end in _SLIDE_EXTS:
        return "material"

    # Recordings
    if _RECORDING_RE.search(url):
        return "recording"

    # Known service — skip crawling
    if _is_service_domain(url):
        return "skip"

    # Nav link (same-domain, looks like a schedule/lecture page)
    if _NAV_KEYWORDS.search(url) or _NAV_KEYWORDS.search(text):
        return "nav"

    return "skip"


def _google_export_url(url: str) -> str | None:
    """Convert a Google Slides/Docs URL to its PDF export URL."""
    m = re.search(r"docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return f"https://docs.google.com/presentation/d/{m.group(1)}/export/pdf"
    m = re.search(r"docs\.google\.com/document/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return f"https://docs.google.com/document/d/{m.group(1)}/export?format=pdf"
    return None


class CourseSiteCrawler:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NeuronBot/1.0)"},
        )
        self._file_ingester = FileIngester()

    def crawl(
        self,
        root_url: str,
        course_name: str,
        code: str,
        max_pages: int = 40,
    ) -> list[Document]:
        """Crawl a course website and return extracted documents."""
        root_domain = _root_domain(root_url)
        if not root_domain:
            return []

        visited: set[str] = set()
        material_seen: set[str] = set()
        docs: list[Document] = []

        # BFS: (url, depth)
        queue: list[tuple[str, int]] = [(root_url, 0)]

        print(f"      [site] Crawling {root_url}")

        while queue and len(visited) < max_pages:
            url, depth = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)

            try:
                resp = self._client.get(url)
                if resp.status_code != 200:
                    continue
                ct = resp.headers.get("content-type", "")
                if "html" not in ct:
                    continue
                html = resp.text
            except Exception as e:
                print(f"      [site] fetch error {url}: {e}")
                continue

            links = _extract_links(html, url)

            for link_url, link_text in links:
                link_domain = _root_domain(link_url)
                kind = _classify_link(link_url, link_text)

                if kind == "material" and link_url not in material_seen:
                    material_seen.add(link_url)
                    new_docs = self._fetch_material(
                        link_url, link_text, course_name, code, url
                    )
                    docs.extend(new_docs)

                elif kind == "recording" and link_url not in material_seen:
                    material_seen.add(link_url)
                    docs.append(self._recording_stub(
                        link_url, link_text, course_name, code, url
                    ))
                    print(f"      [site] 🎥 {link_text or link_url[:60]}")

                elif kind == "nav" and depth < 1 and link_domain == root_domain:
                    if link_url not in visited:
                        queue.append((link_url, depth + 1))

        if docs:
            print(f"      [site] → {len(docs)} items from {root_url}")
        else:
            print(f"      [site] → nothing found at {root_url}")

        return docs

    def _fetch_material(
        self,
        url: str,
        label: str,
        course_name: str,
        code: str,
        source_page: str,
    ) -> list[Document]:
        # Remap Google Slides/Docs to export URL
        export_url = _google_export_url(url)
        download_url = export_url or url
        suffix = ".pdf" if export_url else PurePosixPath(urlparse(url).path).suffix.lower()
        if suffix not in _SLIDE_EXTS:
            suffix = ".pdf"

        try:
            resp = self._client.get(download_url, timeout=60)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            # If the response isn't binary content, skip
            if "html" in ct and not export_url:
                return []

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(resp.content)
                tmp_path = tmp.name

            file_docs = self._file_ingester.ingest(tmp_path)
            Path(tmp_path).unlink(missing_ok=True)

            results = []
            for d in file_docs:
                if len(d.content.strip()) < 50:
                    continue
                results.append(Document(
                    id=f"course_site_{_h(url)}",
                    content=d.content,
                    source="canvas",
                    title=f"{code}: {label or Path(urlparse(url).path).name}",
                    metadata={
                        "course": course_name,
                        "course_code": code,
                        "type": "external_slide",
                        "url": url,
                        "page_url": source_page,
                    },
                ))
            if results:
                print(f"      [site] ✓ {label or url[:60]}")
            return results

        except Exception as e:
            print(f"      [site] ✗ {label or url[:60]}: {e}")
            return []

    def _recording_stub(
        self,
        url: str,
        label: str,
        course_name: str,
        code: str,
        source_page: str,
    ) -> Document:
        title = label or url
        content = f"Lecture recording: {title}\nURL: {url}\nFrom course: {course_name}"
        return Document(
            id=f"course_site_rec_{_h(url)}",
            content=content,
            source="canvas",
            title=f"{code}: [Recording] {title}",
            metadata={
                "course": course_name,
                "course_code": code,
                "type": "recording",
                "url": url,
                "page_url": source_page,
            },
        )
