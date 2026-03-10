import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

console = Console()


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 50, min_chunk: int = 100) -> list[str]:
    """
    Split text into semantic chunks on paragraph and sentence boundaries.

    Strategy:
    1. Split on blank lines (paragraph boundaries).
    2. Accumulate paragraphs until approaching chunk_size (max 1000 chars).
    3. When a chunk would exceed chunk_size, flush it and start a new one
       with the last `overlap` characters of the previous chunk prepended
       for context continuity.
    4. Paragraphs longer than chunk_size are split at sentence boundaries
       (". " or "\n\n") — never in the middle of a sentence.
    5. Chunks shorter than min_chunk (100 chars) are merged into the next chunk.
    """
    import re as _re

    if len(text) <= chunk_size:
        return [text] if len(text) >= min_chunk else ([text] if text.strip() else [])

    def _split_at_sentence_boundary(text: str, max_len: int) -> str:
        """Return the longest prefix of text that ends at a sentence boundary and is <= max_len."""
        if len(text) <= max_len:
            return text
        # Look for ". " or "\n\n" boundary within the allowed length
        candidate = text[:max_len]
        # Try ". " boundary (scan backwards)
        idx = candidate.rfind(". ")
        if idx > 0:
            return candidate[:idx + 1]
        # Try "\n\n" boundary
        idx2 = candidate.rfind("\n\n")
        if idx2 > 0:
            return candidate[:idx2]
        # Try "! " or "? " boundary
        for marker in ("! ", "? "):
            idx3 = candidate.rfind(marker)
            if idx3 > 0:
                return candidate[:idx3 + 1]
        # No sentence boundary found — fall back to hard limit
        return candidate

    # Split into paragraphs (one or more blank lines)
    raw_paragraphs = _re.split(r"\n{2,}", text)
    paragraphs: list[str] = []
    for para in raw_paragraphs:
        para = para.strip()
        if not para:
            continue
        # Split overlong paragraphs at sentence boundaries
        if len(para) > chunk_size:
            # Split on sentence-ending punctuation followed by space/newline
            sentences = _re.split(r"(?<=[.!?])\s+", para)
            buf = ""
            for sent in sentences:
                if len(buf) + len(sent) + 1 <= chunk_size:
                    buf = (buf + " " + sent).strip() if buf else sent
                else:
                    if buf:
                        paragraphs.append(buf)
                    # If a single sentence is still too long, split at sentence boundary
                    while len(sent) > chunk_size:
                        part = _split_at_sentence_boundary(sent, chunk_size)
                        paragraphs.append(part)
                        sent = sent[len(part):].lstrip()
                    buf = sent
            if buf:
                paragraphs.append(buf)
        else:
            paragraphs.append(para)

    if not paragraphs:
        return [text[:chunk_size]] if text.strip() else []

    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0
    tail = ""  # overlap text from the previous chunk

    for para in paragraphs:
        # Would adding this paragraph exceed the limit?
        additional = len(para) + (1 if current_parts else 0)  # +1 for newline separator
        if current_len + additional > chunk_size and current_parts:
            # Flush current chunk — ensure it ends at a sentence boundary
            chunk = "\n\n".join(current_parts)
            chunks.append(chunk)
            # Prepare overlap: take last `overlap` chars of flushed chunk
            tail = chunk[-overlap:] if len(chunk) > overlap else chunk
            current_parts = []
            current_len = 0

            # Prepend tail to new chunk for context continuity
            if tail and not para.startswith(tail[-20:]):
                current_parts = [tail, para]
                current_len = len(tail) + 2 + len(para)
            else:
                current_parts = [para]
                current_len = len(para)
        else:
            current_parts.append(para)
            current_len += additional

    # Flush remaining
    if current_parts:
        chunk = "\n\n".join(current_parts)
        if len(chunk) < min_chunk and chunks:
            # Merge tiny trailing chunk into the previous one
            chunks[-1] = chunks[-1] + "\n\n" + chunk
        else:
            chunks.append(chunk)

    # Final pass: drop any chunks below min_chunk (shouldn't happen, but guard)
    return [c for c in chunks if len(c) >= min_chunk] or [text[:chunk_size]]


def is_low_quality_chunk(text: str) -> bool:
    """Filter out noise chunks that add no value."""
    text_lower = text.lower().strip()

    # Too short to be useful
    if len(text.strip()) < 80:
        return True

    # Email boilerplate patterns
    noise_patterns = [
        "unsubscribe", "click here to unsubscribe", "view in browser",
        "you are receiving this", "to opt out", "privacy policy",
        "calendar invite", "accepted your invitation", "declined your invitation",
        "this is an automated", "do not reply to this email",
        "sent from my iphone", "sent from my ipad",
        "get outlook for", "confidentiality notice",
        "this email and any attachments", "privileged and confidential",
    ]
    if any(p in text_lower for p in noise_patterns):
        # Only filter if these are the MAIN content (not just in a footer)
        if len(text.strip()) < 300:
            return True

    # Calendar event with no description (just metadata)
    if text_lower.startswith("[calendar:") and len(text.strip()) < 150:
        return True

    # Mostly whitespace or repeated characters
    non_space = len(text.replace(" ", "").replace("\n", ""))
    if non_space < 50:
        return True

    return False


def _store_docs(docs, label: str):
    from datetime import datetime, timezone
    from .storage.store import NeuronStore
    from .config import CHROMA_DIR
    store = NeuronStore(CHROMA_DIR)
    chunks, metadatas, ids = [], [], []
    seen: set[str] = set()
    skipped = 0
    ingested_at = datetime.now(timezone.utc).isoformat()
    for doc in docs:
        prefix = f"[{doc.source.upper()}: {doc.title}]\n\n"
        for i, chunk in enumerate(chunk_text(doc.content)):
            cid = f"{doc.id}_c{i}"
            if cid not in seen:
                if is_low_quality_chunk(chunk):
                    skipped += 1
                    continue
                seen.add(cid)
                chunks.append(prefix + chunk)
                metadata = {**doc.metadata}
                metadata.setdefault("created_at", ingested_at)
                metadata.setdefault("ingested_at", ingested_at)
                metadata["title"] = doc.title
                metadata["source"] = doc.source
                metadatas.append(metadata)
                ids.append(cid)
    if chunks:
        store.upsert(chunks, metadatas, ids)
        skip_note = f", {skipped} noise chunks skipped" if skipped else ""
        console.print(f"[green]✓ {label}: {len(chunks)} chunks from {len(docs)} documents{skip_note}[/]")
    else:
        console.print(f"[yellow]No content found.[/]")


# ── ROOT ───────────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """Neuron — your personal intelligence system."""
    pass


# ── QUICK CAPTURE ──────────────────────────────────────────────────────────────

@cli.command()
@click.argument("text")
def note(text):
    """Capture a quick note or idea."""
    from .ingestion.note import NoteIngester
    docs = NoteIngester().ingest(text)
    _store_docs(docs, "Note")
    console.print(f'[dim]"{text[:80]}{"..." if len(text) > 80 else ""}"[/]')


# ── INGEST ─────────────────────────────────────────────────────────────────────

@cli.group()
def ingest():
    """Ingest knowledge from a source."""
    pass


@ingest.command(name="canvas")
def ingest_canvas():
    """Ingest your Canvas LMS courses (assignments, pages, announcements)."""
    from .ingestion.canvas import CanvasIngester
    from .config import CANVAS_API_TOKEN, CANVAS_API_URL
    if not CANVAS_API_TOKEN:
        console.print("[red]Set CANVAS_API_TOKEN in .env[/]"); return
    console.print("[bold blue]Ingesting Canvas...[/]")
    _store_docs(CanvasIngester(CANVAS_API_TOKEN, CANVAS_API_URL).ingest(), "Canvas")


@ingest.command(name="whoop")
@click.option("--days", default=90, show_default=True, help="Number of days to pull")
def ingest_whoop(days):
    """Ingest Whoop health data — recovery, sleep, strain, workouts."""
    from .ingestion.whoop import WhoopIngester
    from .config import WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET
    if not WHOOP_CLIENT_ID or not WHOOP_CLIENT_SECRET:
        console.print("[red]Set WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET in .env[/]"); return
    console.print(f"[bold blue]Connecting to Whoop (last {days} days)...[/]")
    try:
        docs = WhoopIngester(WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET).ingest(days=days)
        if not docs:
            console.print("[yellow]No Whoop data found.[/]"); return
        _store_docs(docs, f"Whoop ({days}d)")
    except Exception as e:
        console.print(f"[red]Error: {e}[/]")


@ingest.command(name="meetings")
@click.option("--csv", "csv_path", default=None, help="Path to Granola CSV export")
def ingest_meetings(csv_path):
    """Ingest meeting notes from Granola exports."""
    from .ingestion.granola import GranolaIngester
    console.print("[bold blue]Ingesting meeting notes...[/]")
    ingester = GranolaIngester()
    docs = ingester.ingest_csv(csv_path) if csv_path else ingester.ingest_all()
    if not docs:
        console.print("[yellow]No meetings found. Pass --csv or place granola-export-*.csv in ~/Personal[/]"); return
    _store_docs(docs, "Meetings")


@ingest.command(name="file")
@click.argument("path")
def ingest_file(path):
    """Ingest a file — PDF, txt, md, docx."""
    from .ingestion.file import FileIngester
    console.print(f"[bold blue]Ingesting {path}...[/]")
    try:
        _store_docs(FileIngester().ingest(path), path)
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="folder")
@click.argument("path")
@click.option("--no-recurse", is_flag=True, default=False)
@click.option("--source", default="folder", help="Source label (e.g. notion, gdrive)")
def ingest_folder(path, no_recurse, source):
    """Ingest all documents in a folder or ZIP export (Notion, Google Drive, etc.)."""
    import zipfile, tempfile, os
    from .ingestion.folder import FolderIngester

    target = path
    tmp_dir = None

    # Auto-extract ZIP files
    if path.endswith(".zip"):
        console.print(f"[dim]Extracting {path}...[/]")
        tmp_dir = tempfile.mkdtemp()
        with zipfile.ZipFile(path, 'r') as z:
            z.extractall(tmp_dir)
        target = tmp_dir
        if source == "folder":
            source = "notion"  # ZIP exports are almost always Notion

    console.print(f"[bold blue]Ingesting {target} (source={source})...[/]")
    try:
        docs = FolderIngester().ingest(target, recursive=not no_recurse, source=source)
        _store_docs(docs, f"{source} export ({len(docs)} docs)")
    except Exception as e:
        console.print(f"[red]{e}[/]")
    finally:
        if tmp_dir:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)


@ingest.command(name="goodnotes")
@click.argument("path", required=False, default=None,
                metavar="[PATH]")
def ingest_goodnotes(path):
    """Ingest GoodNotes notebooks.

    \b
    Auto-discovers from iCloud if no path given:
      neuron ingest goodnotes

    \b
    Or point at any folder of exported PDFs:
      neuron ingest goodnotes ~/Downloads/GoodNotes

    \b
    For best results, enable Auto-Backup in GoodNotes:
      GoodNotes > Settings > Auto-backup > iCloud Drive > GoodNotes folder
    """
    from .ingestion.goodnotes import GoodNotesIngester
    console.print("[bold blue]Ingesting GoodNotes…[/]")
    try:
        docs = GoodNotesIngester().ingest(path)
        if docs:
            _store_docs(docs, f"GoodNotes ({len(docs)} notebooks)")
        else:
            console.print("[yellow]No text found. GoodNotes handwriting requires export with text recognition.[/]")
            console.print("[dim]In GoodNotes: long-press a notebook > Export > PDF > enable 'Export handwriting as text'[/]")
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/]")


@ingest.command(name="url")
@click.argument("url")
def ingest_url(url):
    """Ingest a web article or page."""
    from .ingestion.web import WebIngester
    console.print(f"[bold blue]Fetching {url}...[/]")
    try:
        docs = WebIngester().ingest(url)
        _store_docs(docs, docs[0].title if docs else url)
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="youtube")
@click.argument("url")
def ingest_youtube(url):
    """Ingest a YouTube video transcript."""
    from .ingestion.youtube import YouTubeIngester
    console.print(f"[bold blue]Fetching transcript for {url}...[/]")
    try:
        docs = YouTubeIngester().ingest(url)
        _store_docs(docs, docs[0].title if docs else url)
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="kindle")
@click.option("--path", default=None, help="Path to My Clippings.txt")
def ingest_kindle(path):
    """Ingest Kindle highlights from My Clippings.txt."""
    from .ingestion.kindle import KindleIngester
    console.print("[bold blue]Ingesting Kindle highlights...[/]")
    try:
        docs = KindleIngester().ingest(path)
        _store_docs(docs, f"Kindle ({len(docs)} books)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="kindle-cloud")
def ingest_kindle_cloud():
    """Ingest Kindle highlights from Amazon cloud (read.amazon.com).

    Opens a browser window on first run — log in to Amazon, then close
    the window when the scrape completes. Subsequent runs reuse your session.

    Requires: pip install playwright && playwright install chromium
    """
    from .ingestion.kindle_cloud import KindleCloudIngester
    console.print("[bold blue]Ingesting Kindle highlights from Amazon cloud...[/]")
    console.print("[dim]A browser window will open. Log in if prompted.[/]")
    try:
        docs = KindleCloudIngester().ingest()
        _store_docs(docs, f"Kindle Cloud ({len(docs)} books)")
    except ImportError as e:
        console.print(f"[red]{e}[/]")
        console.print("[dim]Install with: pip install playwright && playwright install chromium[/]")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="readwise")
def ingest_readwise():
    """Ingest all Readwise highlights (Kindle, Instapaper, Pocket, etc.)."""
    from .ingestion.readwise import ReadwiseIngester
    from .config import READWISE_API_TOKEN
    if not READWISE_API_TOKEN:
        console.print("[red]Set READWISE_API_TOKEN in .env — get it at readwise.io/access_token[/]"); return
    console.print("[bold blue]Ingesting Readwise highlights...[/]")
    try:
        docs = ReadwiseIngester(READWISE_API_TOKEN).ingest()
        _store_docs(docs, f"Readwise ({len(docs)} sources)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="notion")
def ingest_notion():
    """Ingest all your Notion pages."""
    from .ingestion.notion import NotionIngester
    from .config import NOTION_API_TOKEN
    if not NOTION_API_TOKEN:
        console.print("[red]Set NOTION_API_TOKEN in .env — create an integration at notion.so/my-integrations[/]"); return
    console.print("[bold blue]Ingesting Notion pages...[/]")
    try:
        docs = NotionIngester(NOTION_API_TOKEN).ingest()
        _store_docs(docs, f"Notion ({len(docs)} pages)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="github")
@click.argument("repo")
def ingest_github(repo):
    """Ingest a GitHub repo's README and issues. Format: owner/repo"""
    from .ingestion.github import GitHubIngester
    from .config import GITHUB_TOKEN
    console.print(f"[bold blue]Ingesting {repo}...[/]")
    try:
        docs = GitHubIngester(GITHUB_TOKEN).ingest_repo(repo)
        _store_docs(docs, repo)
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="podcast")
@click.argument("rss_url")
@click.option("--limit", default=20, help="Max episodes to ingest")
def ingest_podcast(rss_url, limit):
    """Ingest a podcast feed via RSS URL."""
    from .ingestion.rss import RSSIngester
    console.print(f"[bold blue]Fetching podcast feed...[/]")
    try:
        docs = RSSIngester().ingest(rss_url, limit=limit)
        _store_docs(docs, f"Podcast ({len(docs)} episodes)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="notes")
def ingest_notes():
    """Ingest all your Apple Notes (batched, shows progress)."""
    from .ingestion.apple_notes import AppleNotesIngester
    console.print("[bold blue]Ingesting Apple Notes...[/]")
    try:
        def progress(start, end, total):
            console.print(f"  [dim]Fetching notes {start}–{end} of {total}...[/]")
        docs = AppleNotesIngester().ingest(on_progress=progress)
        _store_docs(docs, f"Apple Notes ({len(docs)} notes)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="bookmarks")
@click.option("--no-fetch", is_flag=True, default=False, help="Save URLs only, don't fetch page content")
@click.option("--limit", default=50, help="Max pages to fetch")
def ingest_bookmarks(no_fetch, limit):
    """Ingest Chrome bookmarks (fetches page content by default)."""
    from .ingestion.bookmarks import BookmarksIngester
    console.print("[bold blue]Ingesting Chrome bookmarks...[/]")
    try:
        docs = BookmarksIngester().ingest_chrome(fetch_content=not no_fetch, limit=limit)
        _store_docs(docs, f"Bookmarks ({len(docs)} pages)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="files")
@click.argument("directories", nargs=-1)
@click.option("--home", is_flag=True, default=False, help="Scan ~/Documents, ~/Desktop, ~/Downloads")
def ingest_files(directories, home):
    """Mass-scan directories for PDF, DOCX, TXT, MD files.

    Examples:
      neuron ingest files ~/Documents ~/Desktop
      neuron ingest files --home
      neuron ingest files ~/Google\\ Drive
    """
    from .ingestion.files_scanner import FileScannerIngester
    import os

    dirs = list(directories)
    if home:
        dirs += [
            os.path.expanduser("~/Documents"),
            os.path.expanduser("~/Desktop"),
            os.path.expanduser("~/Downloads"),
        ]
    if not dirs:
        console.print("[red]Specify directories or use --home[/]")
        return

    console.print(f"[bold blue]Scanning {len(dirs)} director{'y' if len(dirs)==1 else 'ies'}...[/]")

    from rich.progress import Progress, SpinnerColumn, TextColumn
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
        task = progress.add_task("Scanning...", total=None)

        def on_progress(path, status):
            short = path.split("/")[-1]
            progress.update(task, description=f"[dim]{short}[/]")

        docs, stats = FileScannerIngester().scan(dirs, on_progress=on_progress)

    console.print(f"[bold]Scan complete:[/] {stats['found']} files found, {stats['ingested']} ingested, {stats['failed']} failed")
    if stats["by_ext"]:
        for ext, count in sorted(stats["by_ext"].items(), key=lambda x: -x[1]):
            console.print(f"  [dim]{ext}[/]: {count} files")
    _store_docs(docs, f"Files ({stats['ingested']} files)")


@ingest.command(name="youtube-liked")
@click.argument("json_path")
@click.option("--limit", default=200, help="Max videos to process")
def ingest_youtube_liked(json_path, limit):
    """Ingest YouTube liked videos from Google Takeout JSON export.

    Export from https://takeout.google.com — select YouTube, liked videos playlist.
    Then: neuron ingest youtube-liked 'Liked videos.json'
    """
    from .ingestion.youtube_liked import YouTubeLikedIngester
    console.print(f"[bold blue]Ingesting YouTube liked videos from {json_path}...[/]")
    console.print("[dim]This may take a while — fetching transcripts for each video.[/]")
    try:
        ingester = YouTubeLikedIngester()
        docs, total, failed = ingester.ingest_from_takeout(json_path, limit=limit)
        console.print(f"[dim]Processed {total} videos, {failed} failed (no transcript/private)[/]")
        _store_docs(docs, f"YouTube Liked ({len(docs)} videos)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="twitter")
@click.argument("path")
def ingest_twitter(path):
    """Ingest your Twitter/X archive. Pass tweets.js, a folder, or the archive ZIP."""
    from .ingestion.twitter import TwitterIngester
    console.print(f"[bold blue]Ingesting Twitter archive from {path}...[/]")
    try:
        docs = TwitterIngester().ingest(path)
        _store_docs(docs, f"Twitter ({len(docs)} tweets)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="instagram")
@click.argument("path")
def ingest_instagram(path):
    """Ingest Instagram data export (folder or ZIP)."""
    from .ingestion.instagram import InstagramIngester
    console.print(f"[bold blue]Ingesting Instagram export from {path}...[/]")
    try:
        docs = InstagramIngester().ingest(path)
        _store_docs(docs, f"Instagram ({len(docs)} posts)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="tiktok")
@click.argument("path")
def ingest_tiktok(path):
    """Ingest TikTok data export. Pass user_data.json from TikTok export."""
    from .ingestion.tiktok import TikTokIngester
    console.print(f"[bold blue]Ingesting TikTok data from {path}...[/]")
    try:
        docs = TikTokIngester().ingest(path)
        _store_docs(docs, f"TikTok ({len(docs)} items)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="goodreads")
@click.argument("path")
def ingest_goodreads(path):
    """Ingest Goodreads library export CSV (goodreads_library_export.csv)."""
    from .ingestion.goodreads import GoodreadsIngester
    console.print(f"[bold blue]Ingesting Goodreads library from {path}...[/]")
    try:
        docs = GoodreadsIngester().ingest(path)
        _store_docs(docs, f"Goodreads ({len(docs)} books)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="letterboxd")
@click.argument("path")
def ingest_letterboxd(path):
    """Ingest Letterboxd data export (ZIP or extracted folder)."""
    from .ingestion.letterboxd import LetterboxdIngester
    console.print(f"[bold blue]Ingesting Letterboxd data from {path}...[/]")
    try:
        docs = LetterboxdIngester().ingest(path)
        _store_docs(docs, f"Letterboxd ({len(docs)} films)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="spotify")
def ingest_spotify():
    """Ingest Spotify saved tracks and podcasts (OAuth — opens browser on first run)."""
    from .ingestion.spotify import SpotifyIngester
    from .config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        console.print("[red]Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env[/]")
        console.print("[dim]Create an app at developer.spotify.com/dashboard — set redirect URI to http://localhost:8888/callback[/]")
        return
    console.print("[bold blue]Connecting to Spotify...[/]")
    try:
        docs = SpotifyIngester(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET).ingest()
        _store_docs(docs, f"Spotify ({len(docs)} items)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="pocket")
def ingest_pocket():
    """Ingest saved articles from Pocket (requires POCKET_CONSUMER_KEY + POCKET_ACCESS_TOKEN in .env)."""
    from .ingestion.pocket import PocketIngester
    from .config import POCKET_CONSUMER_KEY, POCKET_ACCESS_TOKEN
    if not POCKET_CONSUMER_KEY or not POCKET_ACCESS_TOKEN:
        console.print("[red]Set POCKET_CONSUMER_KEY and POCKET_ACCESS_TOKEN in .env[/]")
        console.print("[dim]Get your access token at getpocket.com/developer[/]")
        return
    console.print("[bold blue]Fetching Pocket articles...[/]")
    try:
        docs = PocketIngester(POCKET_CONSUMER_KEY, POCKET_ACCESS_TOKEN).ingest()
        _store_docs(docs, f"Pocket ({len(docs)} articles)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="trakt")
def ingest_trakt():
    """Ingest watched movies & shows from Trakt.tv (requires TRAKT_CLIENT_ID + TRAKT_USERNAME in .env)."""
    from .ingestion.trakt import TraktIngester
    from .config import TRAKT_CLIENT_ID, TRAKT_USERNAME
    if not TRAKT_CLIENT_ID or not TRAKT_USERNAME:
        console.print("[red]Set TRAKT_CLIENT_ID and TRAKT_USERNAME in .env[/]")
        console.print("[dim]Get a client ID at trakt.tv/oauth/applications[/]")
        return
    console.print("[bold blue]Fetching Trakt watch history, ratings, and watchlist...[/]")
    try:
        docs = TraktIngester(TRAKT_CLIENT_ID, TRAKT_USERNAME).ingest()
        _store_docs(docs, f"Trakt ({len(docs)} items)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="netflix")
@click.argument("path")
def ingest_netflix(path):
    """Ingest Netflix viewing history CSV.

    Download: netflix.com/viewingactivity → click 'Download All'
    """
    from .ingestion.netflix import NetflixIngester
    console.print(f"[bold blue]Ingesting Netflix history from {path}...[/]")
    try:
        docs = NetflixIngester().ingest(path)
        _store_docs(docs, f"Netflix ({len(docs)} items)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="gcal")
@click.option("--account", default=None, help="Google account email (for multiple accounts)")
@click.option("--days-past", default=180, help="Days of past events to ingest")
@click.option("--days-future", default=90, help="Days of future events to ingest")
def ingest_gcal(account, days_past, days_future):
    """Ingest Google Calendar events (all accounts, past 6 months + next 3 months).

    First run: opens browser for Google auth. Set GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET in .env.
    Subsequent runs: uses saved token, auto-refreshes.
    Run again with --account to add a second Google account.
    """
    from .ingestion.google_auth import get_credentials, get_all_credentials
    from .ingestion.google_calendar import GoogleCalendarIngester
    from .config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        console.print("[red]Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env[/]")
        console.print("[dim]Create credentials at console.cloud.google.com → APIs & Services → Credentials → OAuth 2.0 Client ID (Desktop)[/]")
        return
    console.print("[bold blue]Ingesting Google Calendar...[/]")
    try:
        if account:
            creds = get_credentials(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, account)
            docs = GoogleCalendarIngester(creds, account_label=account).ingest(days_past=days_past, days_future=days_future)
            _store_docs(docs, f"Google Calendar ({account})")
        else:
            accounts = get_all_credentials(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
            if not accounts:
                creds = get_credentials(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
                docs = GoogleCalendarIngester(creds, account_label="primary").ingest(days_past=days_past, days_future=days_future)
                _store_docs(docs, "Google Calendar")
            else:
                for label, creds in accounts:
                    docs = GoogleCalendarIngester(creds, account_label=label).ingest(days_past=days_past, days_future=days_future)
                    _store_docs(docs, f"Google Calendar ({label})")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="gmail")
@click.option("--account", default=None, help="Google account email (for multiple accounts)")
@click.option("--days", default=60, help="Days of email history to ingest")
def ingest_gmail(account, days):
    """Ingest Gmail — sent mail and starred messages.

    Sent mail is highest signal (reflects your thinking and decisions).
    First run: opens browser for Google auth. Set GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET in .env.
    Run again with --account to add a second Google account.
    """
    from .ingestion.google_auth import get_credentials, get_all_credentials
    from .ingestion.gmail import GmailIngester
    from .config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        console.print("[red]Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env[/]")
        console.print("[dim]Create credentials at console.cloud.google.com → APIs & Services → Credentials → OAuth 2.0 Client ID (Desktop)[/]")
        return
    console.print("[bold blue]Ingesting Gmail...[/]")
    try:
        if account:
            creds = get_credentials(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, account)
            docs = GmailIngester(creds, account_label=account).ingest(days=days)
            _store_docs(docs, f"Gmail ({account})")
        else:
            accounts = get_all_credentials(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
            if not accounts:
                creds = get_credentials(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
                docs = GmailIngester(creds, account_label="primary").ingest(days=days)
                _store_docs(docs, "Gmail")
            else:
                for label, creds in accounts:
                    docs = GmailIngester(creds, account_label=label).ingest(days=days)
                    _store_docs(docs, f"Gmail ({label})")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="gdrive")
@click.option("--account", default=None, help="Google account email (for multiple accounts)")
@click.option("--days", default=365, help="Days since last modification to include")
@click.option("--owned-only", is_flag=True, default=False, help="Only include files you own")
def ingest_gdrive(account, days, owned_only):
    """Ingest Google Drive — Docs, Sheets, and Slides you've recently edited.

    First run: opens browser for Google auth. Set GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET in .env.
    """
    from .ingestion.google_auth import get_credentials, get_all_credentials
    from .ingestion.google_drive import GoogleDriveIngester
    from .config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        console.print("[red]Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env[/]")
        return
    console.print("[bold blue]Ingesting Google Drive...[/]")
    try:
        if account:
            creds = get_credentials(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, account)
            docs = GoogleDriveIngester(creds, account_label=account).ingest(days=days, owned_only=owned_only)
            _store_docs(docs, f"Google Drive ({account})")
        else:
            accounts = get_all_credentials(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
            if not accounts:
                creds = get_credentials(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
                docs = GoogleDriveIngester(creds, account_label="primary").ingest(days=days, owned_only=owned_only)
                _store_docs(docs, "Google Drive")
            else:
                for label, creds in accounts:
                    docs = GoogleDriveIngester(creds, account_label=label).ingest(days=days, owned_only=owned_only)
                    _store_docs(docs, f"Google Drive ({label})")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="photos")
@click.option("--ai-describe", is_flag=True, default=False, help="Use Claude Haiku vision for photos without on-device descriptions")
@click.option("--limit", default=None, type=int, help="Max assets to process")
@click.option("--since", default=None, help="Only assets after this date (YYYY-MM-DD)")
@click.option("--no-videos", is_flag=True, default=False, help="Skip video transcription")
def ingest_photos(ai_describe, limit, since, no_videos):
    """Ingest Apple Photos library (metadata + on-device descriptions; --ai-describe for Claude vision)."""
    from .ingestion.photos import PhotosIngester
    console.print("[bold blue]Ingesting Apple Photos...[/]")
    try:
        docs = PhotosIngester().ingest(ai_describe=ai_describe, limit=limit, since=since, include_videos=not no_videos)
        _store_docs(docs, f"Photos ({len(docs)} assets)")
    except Exception as e:
        console.print(f"[red]{e}[/]")


@ingest.command(name="audio")
@click.argument("path", default=str(
    __import__("pathlib").Path.home() / "Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
))
@click.option("--source", default="voice_memo", help="Source label (default: voice_memo)")
def ingest_audio(path, source):
    """Ingest voice memos or audio files from a directory (default: Apple Voice Memos). Requires faster-whisper."""
    from .ingestion.audio import AudioIngester
    console.print(f"[bold blue]Transcribing audio files in {path}...[/]")
    try:
        docs = AudioIngester().ingest(path, source=source)
        _store_docs(docs, f"{source} ({len(docs)} files)")
    except ImportError:
        console.print("[red]Install faster-whisper: pip install faster-whisper[/]")
    except Exception as e:
        console.print(f"[red]{e}[/]")


# ── REFRESH ────────────────────────────────────────────────────────────────────

@cli.command()
def refresh():
    """Re-run all live ingesters (Spotify, Pocket, Trakt, Canvas, Notion, Readwise)."""
    from .config import (
        CANVAS_API_TOKEN, CANVAS_API_URL,
        NOTION_API_TOKEN, READWISE_API_TOKEN,
        POCKET_CONSUMER_KEY, POCKET_ACCESS_TOKEN,
        TRAKT_CLIENT_ID, TRAKT_USERNAME,
        SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET,
    )

    def _run(label, fn):
        try:
            docs = fn()
            _store_docs(docs, label)
        except Exception as e:
            console.print(f"[yellow]{label}: {e}[/]")

    if CANVAS_API_TOKEN:
        from .ingestion.canvas import CanvasIngester
        _run("Canvas", lambda: CanvasIngester(CANVAS_API_TOKEN, CANVAS_API_URL).ingest())

    if NOTION_API_TOKEN:
        from .ingestion.notion import NotionIngester
        _run("Notion", lambda: NotionIngester(NOTION_API_TOKEN).ingest())

    if READWISE_API_TOKEN:
        from .ingestion.readwise import ReadwiseIngester
        _run("Readwise", lambda: ReadwiseIngester(READWISE_API_TOKEN).ingest())

    if POCKET_CONSUMER_KEY and POCKET_ACCESS_TOKEN:
        from .ingestion.pocket import PocketIngester
        _run("Pocket", lambda: PocketIngester(POCKET_CONSUMER_KEY, POCKET_ACCESS_TOKEN).ingest())

    if TRAKT_CLIENT_ID and TRAKT_USERNAME:
        from .ingestion.trakt import TraktIngester
        _run("Trakt", lambda: TraktIngester(TRAKT_CLIENT_ID, TRAKT_USERNAME).ingest())

    if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
        from .ingestion.spotify import SpotifyIngester
        _run("Spotify", lambda: SpotifyIngester(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET).ingest())

    from .config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
        from .ingestion.google_auth import get_all_credentials
        from .ingestion.google_calendar import GoogleCalendarIngester
        from .ingestion.gmail import GmailIngester
        accounts = get_all_credentials(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
        for label, creds in accounts:
            _run(f"Google Calendar ({label})", lambda c=creds, l=label: GoogleCalendarIngester(c, l).ingest())
            _run(f"Gmail ({label})", lambda c=creds, l=label: GmailIngester(c, l).ingest(days=30))


# ── DIGEST ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--no-refresh", is_flag=True, default=False, help="Skip live ingestion, use existing data")
def digest(no_refresh):
    """Daily briefing: priorities, recent activity, and one insight worth revisiting."""
    from .retrieval.engine import NeuronEngine
    from datetime import datetime

    if not no_refresh:
        console.print("[dim]Refreshing live sources...[/]")
        from click.testing import CliRunner
        CliRunner().invoke(refresh, [])

    console.print(f"\n[dim]Building your briefing for {datetime.now().strftime('%A, %B %-d')}...[/]\n")
    result = NeuronEngine().digest()
    console.print(Panel(
        Markdown(result["result"]),
        title=f"[bold]🧠 Neuron Daily Briefing — {datetime.now().strftime('%B %-d, %Y')}[/]",
        border_style="cyan",
    ))
    _render_sources(result.get("sources", []))


# ── RETRIEVAL ──────────────────────────────────────────────────────────────────

def _render_sources(sources: list[dict]):
    if not sources:
        return
    console.print("\n[bold dim]Sources:[/]")
    for s in sources:
        icon = s.get("icon", "📌")
        title = s.get("title", "Unknown")[:60]
        src = s.get("source", "")
        idx = s.get("index", "")
        console.print(f"  [dim][{idx}][/] {icon} [cyan]{title}[/] [dim]({src})[/]")


@cli.command()
@click.argument("question")
def ask(question):
    """Ask a question across your entire knowledge base."""
    from .retrieval.engine import NeuronEngine
    console.print("\n[dim]Searching...[/]\n")
    result = NeuronEngine().ask(question)
    console.print(Markdown(result["answer"]))
    _render_sources(result.get("sources", []))


@cli.command()
@click.argument("topic")
def context(topic):
    """Generate a context pack — overview, key concepts, open questions."""
    from .retrieval.engine import NeuronEngine
    console.print(f"\n[dim]Building context pack for '{topic}'...[/]\n")
    result = NeuronEngine().context_pack(topic)
    console.print(Panel(
        Markdown(result["context_pack"]),
        title=f"[bold]Context Pack: {topic}[/]",
        border_style="blue",
    ))
    _render_sources(result.get("sources", []))


@cli.command()
@click.argument("topic")
def resurface(topic):
    """Surface past knowledge related to what you're currently thinking about."""
    from .retrieval.engine import NeuronEngine
    console.print(f"\n[dim]Resurfacing '{topic}'...[/]\n")
    result = NeuronEngine().resurface(topic)
    console.print(Panel(
        Markdown(result["result"]),
        title=f"[bold]Resurfaced: {topic}[/]",
        border_style="yellow",
    ))
    _render_sources(result.get("sources", []))


@cli.command()
@click.argument("topic")
def connections(topic):
    """Find how a topic connects across different sources."""
    from .retrieval.engine import NeuronEngine
    console.print(f"\n[dim]Finding connections for '{topic}'...[/]\n")
    result = NeuronEngine().connections(topic)
    console.print(Panel(
        Markdown(result["result"]),
        title=f"[bold]Connections: {topic}[/]",
        border_style="green",
    ))
    _render_sources(result.get("sources", []))



@cli.command()
@click.option("--days", default=14, help="How many days ahead to look")
def upcoming(days):
    """What's on your calendar in the next N days?"""
    from .retrieval.engine import NeuronEngine
    console.print(f"\n[dim]Fetching next {days} days...[/]\n")
    result = NeuronEngine().upcoming(days=days)
    console.print(Panel(
        Markdown(result["result"]),
        title=f"[bold]Upcoming — Next {days} Days[/]",
        border_style="green",
    ))


@cli.command()
@click.option("--days", default=14, help="How many days back to look")
def recent(days):
    """What have you been taking in lately? Temporal browse — no search needed."""
    from .retrieval.engine import NeuronEngine
    console.print(f"\n[dim]Fetching last {days} days...[/]\n")
    result = NeuronEngine().recent(days=days)
    console.print(Panel(
        Markdown(result["result"]),
        title=f"[bold]Recent — Last {days} Days[/]",
        border_style="cyan",
    ))


# ── STATUS ─────────────────────────────────────────────────────────────────────

@cli.command()
def status():
    """Show knowledge base stats."""
    from .storage.store import NeuronStore
    from .config import CHROMA_DIR
    store = NeuronStore(CHROMA_DIR)
    total = store.count()

    table = Table(title="Neuron Knowledge Base", show_header=True, header_style="bold")
    table.add_column("Source", style="cyan")
    table.add_column("Chunks", justify="right")

    sources = ["canvas", "file", "web", "note", "granola", "youtube", "youtube_liked",
               "kindle", "readwise", "notion", "github", "podcast", "apple_notes", "bookmarks", "folder",
               "twitter", "instagram", "tiktok", "goodreads", "letterboxd", "spotify", "pocket", "trakt",
               "calendar", "gmail", "gdrive"]
    for src in sources:
        try:
            result = store.collection.get(where={"source": src})
            count = len(result["ids"])
            if count > 0:
                table.add_row(src, str(count))
        except Exception:
            pass

    table.add_row("[bold]TOTAL[/]", f"[bold]{total}[/]")
    console.print(table)
    console.print(f"\n[dim]{CHROMA_DIR}[/]")


def _start_chroma_server(chroma_port: int = 8001) -> "subprocess.Popen":
    """Launch chromadb HTTP server in a subprocess to isolate its Rust bindings."""
    import subprocess
    import time
    import sys
    import urllib.request
    from .config import CHROMA_DIR

    chroma_bin = str(__import__("pathlib").Path(sys.executable).parent / "chroma")
    proc = subprocess.Popen(
        [chroma_bin, "run", "--path", str(CHROMA_DIR), "--host", "127.0.0.1", "--port", str(chroma_port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait up to 10s for the chromadb server to be ready
    for _ in range(20):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{chroma_port}/api/v2/heartbeat", timeout=1)
            return proc
        except Exception:
            pass
        if proc.poll() is not None:
            raise RuntimeError(f"chromadb server exited early (code {proc.returncode})")
    raise RuntimeError("chromadb server did not become ready in time")


@cli.command()
@click.option("--host", default="127.0.0.1", help="Host to bind")
@click.option("--port", default=7700, help="Port to listen on")
@click.option("--reload", is_flag=True, default=False)
@click.option("--chroma-port", default=8001, help="Port for internal chromadb HTTP server")
def serve(host, port, reload, chroma_port):
    """Start the Neuron API server (chromadb runs as a separate subprocess)."""
    import os
    import uvicorn

    console.print("[dim]Starting chromadb server...[/]")
    try:
        chroma_proc = _start_chroma_server(chroma_port)
        console.print(f"[dim]chromadb ready on port {chroma_port}[/]")
    except Exception as e:
        console.print(f"[yellow]Warning: could not start chromadb HTTP server ({e}). Falling back to embedded mode.[/]")
        chroma_proc = None

    if chroma_proc is not None:
        os.environ["CHROMA_HTTP_HOST"] = "127.0.0.1"
        os.environ["CHROMA_HTTP_PORT"] = str(chroma_port)

    console.print(f"[bold green]Neuron server starting on http://{host}:{port}[/]")
    try:
        uvicorn.run("neuron.api.server:app", host=host, port=port, reload=reload)
    finally:
        if chroma_proc is not None:
            chroma_proc.terminate()


@cli.command()
@click.option("--port", default=7700, help="Port to serve on")
@click.option("--chroma-port", default=8001, help="Port for internal chromadb HTTP server")
def graph(port, chroma_port):
    """Open the interactive knowledge graph in your browser."""
    import os
    import uvicorn
    import threading
    import webbrowser
    import time

    console.print("[dim]Starting chromadb server...[/]")
    try:
        chroma_proc = _start_chroma_server(chroma_port)
        os.environ["CHROMA_HTTP_HOST"] = "127.0.0.1"
        os.environ["CHROMA_HTTP_PORT"] = str(chroma_port)
    except Exception as e:
        console.print(f"[yellow]Warning: chromadb HTTP server failed ({e}). Using embedded mode.[/]")
        chroma_proc = None

    url = f"http://127.0.0.1:{port}/graph-ui"
    console.print(f"[bold green]Starting Neuron server...[/]")
    console.print(f"[dim]Opening {url}[/]")
    console.print("[dim]Press Ctrl+C to stop.[/]")

    def open_browser():
        time.sleep(1.2)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()
    try:
        uvicorn.run("neuron.api.server:app", host="127.0.0.1", port=port, log_level="error")
    finally:
        if chroma_proc is not None:
            chroma_proc.terminate()
