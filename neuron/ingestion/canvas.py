import re
import tempfile
import httpx
from pathlib import Path
from urllib.parse import urlparse
from .base import Document
from .file import FileIngester
from .course_site import CourseSiteCrawler, _is_service_domain


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def _find_course_site_urls(html: str) -> list[str]:
    """Find external course website URLs in Canvas page HTML.

    Returns URLs that are not documents (PDF/PPTX/etc.) and not known
    services — i.e. they're likely standalone course websites to crawl.
    """
    hrefs = re.findall(r'<a[^>]+href=["\']([^"\']+)["\']', html, re.IGNORECASE)
    sites: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        href = href.strip()
        if not href.startswith(("http://", "https://")):
            continue
        # Skip Canvas itself
        if "courseworks" in href or "canvas" in href:
            continue
        # Skip known document types — handled by _extract_external_doc_urls
        path = urlparse(href).path.lower()
        if any(path.endswith(ext) for ext in (".pdf", ".pptx", ".ppt", ".docx", ".doc")):
            continue
        # Skip Google Slides/Docs — handled separately
        if "docs.google.com" in href:
            continue
        # Skip known service domains
        if _is_service_domain(href):
            continue
        # Deduplicate at the root (scheme + netloc + first path segment)
        parsed = urlparse(href)
        parts = [p for p in parsed.path.split("/") if p]
        root_key = f"{parsed.netloc}/{parts[0] if parts else ''}"
        if root_key not in seen:
            seen.add(root_key)
            sites.append(href)
    return sites


def _extract_external_doc_urls(html: str) -> list[tuple[str, str]]:
    """Extract (url, label) pairs for downloadable docs linked in Canvas page HTML.

    Finds:
    - Google Slides presentations  → export as PDF
    - Google Docs                  → export as PDF
    - Direct .pdf links
    """
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    # All <a href="..."> tags with optional link text
    links = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>', html, re.IGNORECASE)

    for raw_url, label in links:
        url = raw_url.strip()
        label = re.sub(r"\s+", " ", label).strip()

        # Google Slides — convert to PDF export URL
        m = re.search(r'docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)', url)
        if m:
            doc_id = m.group(1)
            export_url = f"https://docs.google.com/presentation/d/{doc_id}/export/pdf"
            if export_url not in seen:
                seen.add(export_url)
                results.append((export_url, label or f"Google Slides {doc_id[:8]}"))
            continue

        # Google Docs — convert to PDF export URL
        m = re.search(r'docs\.google\.com/document/d/([a-zA-Z0-9_-]+)', url)
        if m:
            doc_id = m.group(1)
            export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=pdf"
            if export_url not in seen:
                seen.add(export_url)
                results.append((export_url, label or f"Google Doc {doc_id[:8]}"))
            continue

        # Direct PDF links (not Canvas internal)
        if url.lower().endswith(".pdf") and "courseworks" not in url and url not in seen:
            seen.add(url)
            results.append((url, label or Path(url).name))

    return results


class CanvasIngester:
    def __init__(self, api_token: str, api_url: str):
        self.headers = {"Authorization": f"Bearer {api_token}"}
        self.base = api_url.rstrip("/")
        self._crawler = CourseSiteCrawler()
        self._crawled_sites: set[str] = set()  # avoid re-crawling same site across courses

    def _get(self, path: str, params: dict | None = None) -> list | dict:
        url = f"{self.base}{path}"
        results = []
        while url:
            r = httpx.get(url, headers=self.headers, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                results.extend(data)
            else:
                return data
            url = None
            for part in r.headers.get("Link", "").split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")
                    params = None
                    break
        return results

    def ingest(self) -> list[Document]:
        documents = []
        courses = self._get("/courses", params={"enrollment_state": "active", "per_page": 50})
        if not isinstance(courses, list):
            return documents

        for course in courses:
            if not isinstance(course, dict) or "id" not in course:
                continue
            cid = course["id"]
            name = course.get("name", f"Course {cid}")
            code = course.get("course_code", str(cid))
            print(f"  Ingesting {code}...")

            # Pages (text + external linked docs)
            try:
                for page in self._get(f"/courses/{cid}/pages", params={"per_page": 50}):
                    detail = self._get(f"/courses/{cid}/pages/{page['url']}")
                    body_html = detail.get("body", "") or ""
                    content = _strip_html(body_html)
                    if len(content) > 100:
                        page_date = (detail.get("updated_at") or detail.get("created_at") or "")[:10]
                        documents.append(Document(
                            id=f"canvas_page_{cid}_{page['url']}",
                            content=content,
                            source="canvas",
                            title=f"{code}: {page.get('title', 'Page')}",
                            metadata={
                                "course": name, "course_code": code,
                                "type": "page", "url": page.get("html_url", ""),
                                "date": page_date,
                            },
                        ))
                    # Download Google Slides / external PDFs linked from this page
                    documents.extend(
                        self._fetch_external_docs(body_html, cid, name, code, page.get("html_url", ""))
                    )
                    # Crawl any external course websites linked from this page
                    for site_url in _find_course_site_urls(body_html):
                        site_key = urlparse(site_url).netloc
                        if site_key not in self._crawled_sites:
                            self._crawled_sites.add(site_key)
                            documents.extend(
                                self._crawler.crawl(site_url, name, code)
                            )
            except Exception as e:
                if not (isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (403, 404)):
                    print(f"    Pages error: {e}")

            # Assignments
            try:
                for a in self._get(f"/courses/{cid}/assignments", params={"per_page": 50}):
                    content = _strip_html(f"{a.get('name', '')}\n\n{a.get('description', '') or ''}")
                    if len(content) > 50:
                        documents.append(Document(
                            id=f"canvas_assignment_{cid}_{a['id']}",
                            content=content,
                            source="canvas",
                            title=f"{code}: {a.get('name', 'Assignment')}",
                            metadata={
                                "course": name, "course_code": code,
                                "type": "assignment", "due_at": str(a.get("due_at", "")),
                                "url": a.get("html_url", ""),
                            },
                        ))
            except Exception as e:
                if not (isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (403, 404)):
                    print(f"    Assignments error: {e}")

            # Announcements
            try:
                for ann in self._get(
                    f"/courses/{cid}/discussion_topics",
                    params={"only_announcements": True, "per_page": 20},
                ):
                    content = _strip_html(f"{ann.get('title', '')}\n\n{ann.get('message', '') or ''}")
                    if len(content) > 50:
                        ann_date = (ann.get("posted_at") or ann.get("created_at") or "")[:10]
                        documents.append(Document(
                            id=f"canvas_announcement_{cid}_{ann['id']}",
                            content=content,
                            source="canvas",
                            title=f"{code}: {ann.get('title', 'Announcement')}",
                            metadata={"course": name, "course_code": code, "type": "announcement", "date": ann_date},
                        ))
            except Exception as e:
                if not (isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (403, 404)):
                    print(f"    Announcements error: {e}")

            # Files (PDFs, PPTX, DOCX, slides)
            documents.extend(self._ingest_files(cid, name, code))

        return documents

    def _fetch_external_docs(
        self, html: str, cid: int, course_name: str, code: str, page_url: str
    ) -> list[Document]:
        """Download Google Slides / external PDFs linked from a Canvas page."""
        external_links = _extract_external_doc_urls(html)
        if not external_links:
            return []
        docs = []
        file_ingester = FileIngester()
        for url, label in external_links:
            try:
                r = httpx.get(url, timeout=30, follow_redirects=True)
                r.raise_for_status()
                # Detect actual content type
                ct = r.headers.get("content-type", "")
                if "pdf" in ct or url.endswith(".pdf") or "export" in url:
                    suffix = ".pdf"
                else:
                    continue  # skip non-PDF responses
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(r.content)
                    tmp_path = tmp.name
                file_docs = file_ingester.ingest(tmp_path)
                Path(tmp_path).unlink(missing_ok=True)
                for d in file_docs:
                    if len(d.content.strip()) < 50:
                        continue
                    doc_id = f"canvas_ext_{cid}_{_h(url)}"
                    docs.append(Document(
                        id=doc_id,
                        content=d.content,
                        source="canvas",
                        title=f"{code}: {label}",
                        metadata={
                            "course": course_name,
                            "course_code": code,
                            "type": "external_slide",
                            "url": url,
                            "page_url": page_url,
                        },
                    ))
                print(f"      ✓ [external] {label}")
            except Exception as e:
                print(f"      – [external] {label}: {e}")
        return docs

    def _ingest_files(self, cid: int, course_name: str, code: str) -> list[Document]:
        SUPPORTED = {
            "application/pdf": ".pdf",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
            # .ppt (binary) is unsupported by python-pptx; skip it
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        }
        docs = []
        file_ingester = FileIngester()
        try:
            files = self._get(f"/courses/{cid}/files", params={"per_page": 100})
            if not isinstance(files, list):
                return docs
            print(f"    Files: {len(files)} total, filtering for documents...")
            for f in files:
                content_type = f.get("content-type", f.get("content_type", ""))
                suffix = SUPPORTED.get(content_type)
                if not suffix:
                    continue
                url = f.get("url", "")
                display_name = f.get("display_name", f.get("filename", f"file_{f['id']}"))
                html_url = f.get("html_url", "")
                updated_at = (f.get("updated_at") or f.get("created_at") or "")[:10]
                if not url:
                    continue
                try:
                    # Canvas file URLs are pre-signed S3 URLs — don't send auth header
                    r = httpx.get(url, timeout=60, follow_redirects=True)
                    r.raise_for_status()
                    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                        tmp.write(r.content)
                        tmp_path = tmp.name
                    file_docs = file_ingester.ingest(tmp_path)
                    Path(tmp_path).unlink(missing_ok=True)
                    for d in file_docs:
                        docs.append(Document(
                            id=f"canvas_file_{cid}_{f['id']}",
                            content=d.content,
                            source="canvas",
                            title=f"{code}: {display_name}",
                            metadata={
                                "course": course_name,
                                "course_code": code,
                                "type": suffix.lstrip("."),
                                "url": html_url,
                                "date": updated_at,
                            },
                        ))
                    print(f"      ✓ {display_name} ({suffix})")
                except Exception as e:
                    print(f"      ✗ {display_name}: {e}")
        except Exception as e:
            if not (isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (403, 404)):
                print(f"    Files error: {e}")
        return docs
