"""Kindle Cloud ingester — fetches highlights from read.amazon.com/notebook.

Uses Playwright (real browser) so Amazon auth and TLS fingerprinting are handled
naturally. Session is persisted in ~/.jimmy/kindle_browser/ so you only log in once.

Install deps first:
    pip install playwright && playwright install chromium
"""
from pathlib import Path
from .base import Document
from ..config import JIMMY_DATA_DIR

BROWSER_DIR = JIMMY_DATA_DIR / "kindle_browser"
NOTEBOOK_URL = "https://read.amazon.com/notebook"


class KindleCloudIngester:
    def ingest(self) -> list[Document]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ImportError(
                "Playwright not installed.\n"
                "Run: pip install playwright && playwright install chromium"
            )

        BROWSER_DIR.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            # Persistent context → session cookies saved across runs
            # channel="chrome" uses your installed Chrome instead of Playwright's Chromium
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_DIR),
                headless=False,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()

            print(f"Opening {NOTEBOOK_URL}...")
            page.goto(NOTEBOOK_URL, wait_until="domcontentloaded")

            # Wait for login form or book list to appear
            try:
                page.wait_for_selector(
                    "#ap_email, [data-asin], .kp-notebook-library-each-book",
                    timeout=20000,
                )
            except Exception:
                pass

            # If login form is present, wait for the user to sign in
            if page.query_selector("#ap_email"):
                print("\nPlease log in to Amazon in the browser window.")
                print("Waiting up to 3 minutes...")
                try:
                    page.wait_for_selector(
                        "[data-asin], .kp-notebook-library-each-book",
                        timeout=180000,
                    )
                    print("Login detected. Starting scrape...")
                except Exception:
                    context.close()
                    raise RuntimeError("Login timed out — please try again.")

            docs = self._scrape(page)
            context.close()
            return docs

    def _scrape(self, page) -> list[Document]:
        from playwright.sync_api import TimeoutError as PWTimeout

        # Make sure we're on the main notebook page
        if NOTEBOOK_URL not in page.url:
            page.goto(NOTEBOOK_URL, wait_until="domcontentloaded")

        try:
            page.wait_for_selector(
                "[data-asin], .kp-notebook-library-each-book", timeout=15000
            )
        except PWTimeout:
            raise RuntimeError("Book list did not load. Make sure you're logged in.")

        # Collect all books (ASIN + title) from the sidebar
        books = page.evaluate("""
            () => {
                const candidates = [
                    ...document.querySelectorAll('[data-asin]'),
                    ...document.querySelectorAll('.kp-notebook-library-each-book'),
                ];
                const seen = new Set();
                const results = [];
                for (const el of candidates) {
                    const asin = el.getAttribute('data-asin')
                        || el.closest('[data-asin]')?.getAttribute('data-asin')
                        || '';
                    if (!asin || seen.has(asin)) continue;
                    seen.add(asin);
                    const titleEl = el.querySelector(
                        '.kp-notebook-searchable, .a-size-base-plus, h2, h3, .a-truncate-full'
                    );
                    const title = titleEl ? titleEl.innerText.trim() : '';
                    results.push({ asin, title });
                }
                return results;
            }
        """)

        if not books:
            raise RuntimeError(
                "No books found on the page. The page structure may have changed."
            )

        print(f"Found {len(books)} books. Fetching highlights...")
        docs = []

        for book in books:
            asin = book.get("asin", "")
            title = book.get("title", "").strip() or f"Book {asin}"
            if not asin:
                continue

            try:
                url = f"{NOTEBOOK_URL}?asin={asin}&contentType=Note&language=en-US"
                page.goto(url, wait_until="domcontentloaded")

                try:
                    page.wait_for_selector(
                        ".kp-notebook-highlight, #highlight, [id^='highlight'], "
                        ".kp-notebook-empty-state, .kp-notebook-annotations-not-found",
                        timeout=12000,
                    )
                except PWTimeout:
                    print(f"  – {title}: timed out")
                    continue

                highlights = page.evaluate("""
                    () => {
                        const selectors = [
                            '.kp-notebook-highlight',
                            '#highlight',
                            '[id^="highlight-"]',
                            '.kp-notebook-row-separator .a-row',
                        ];
                        for (const sel of selectors) {
                            const els = document.querySelectorAll(sel);
                            if (els.length > 0) {
                                return Array.from(els)
                                    .map(el => el.innerText.trim())
                                    .filter(t => t.length > 15);
                            }
                        }
                        return [];
                    }
                """)

                if highlights:
                    docs.append(Document(
                        id=f"kindle_cloud_{asin}",
                        content="\n\n".join(highlights),
                        source="kindle",
                        title=f"Kindle: {title}",
                        metadata={"type": "book_highlights", "book": title, "asin": asin},
                    ))
                    print(f"  ✓ {title}: {len(highlights)} highlights")
                else:
                    print(f"  – {title}: no highlights")

            except Exception as e:
                print(f"  ✗ {title}: {e}")
                continue

        return docs
