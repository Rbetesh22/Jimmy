import os
import re as _re_module
import random
import hashlib
import time as _time
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..storage.store import JimmyStore
from ..config import CHROMA_DIR, JIMMY_DATA_DIR, JIMMY_USER_NAME, JIMMY_USER_BIO, JIMMY_USER_CONTEXT

# ── Module-level compiled regexes (avoid recompilation per call) ──────────────
_EMOJI_PATTERN = _re_module.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002702-\U000027BF"
    "\u2600-\u2B55"
    "\uFE00-\uFE0F"
    "\uFFFD"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U0001F900-\U0001F9FF"
    "\u2640-\u2642"
    "\u200d"
    "\u23cf\u23e9\u231a"
    "]+",
    flags=_re_module.UNICODE,
)
_TOKENIZE_RE = _re_module.compile(r"[^a-z0-9\s]")


def _get_tier_model(provider: str, tier: str) -> str:
    """Get model name for a provider+tier (used in fallback chain)."""
    from ..config import LLM_TIER_MAP
    models = LLM_TIER_MAP.get(provider, LLM_TIER_MAP["ollama"])
    return models.get(tier, models["default"])

# ── Query expansion cache ─────────────────────────────────────────────────────
_QUERY_CACHE: dict[str, tuple[list[str], float]] = {}
_QUERY_CACHE_TTL = 86400  # 24 hours

def _user_prompt_context() -> str:
    """Return a compact user bio string for LLM prompts."""
    return f"{JIMMY_USER_NAME} — {JIMMY_USER_BIO}"

SOURCE_ICONS = {
    "canvas": "🎓",
    "calendar": "📅",
    "gmail": "✉️",
    "gdrive": "📂",
    "web": "🌐",
    "youtube": "📺",
    "note": "📝",
    "file": "📄",
    "granola": "🎙️",
    "kindle": "📚",
    "readwise": "📖",
    "notion": "🗒️",
    "github": "💻",
    "podcast": "🎧",
    "apple_notes": "📓",
    "folder": "📁",
    "youtube_liked": "👍",
    "spotify": "🎵",
    "twitter": "🐦",
    "instagram": "📸",
    "tiktok": "📱",
    "goodreads": "📗",
    "letterboxd": "🎬",
    "photos": "📷",
    "videos": "🎥",
    "voice_memo": "🎙️",
    "trakt": "🎬",
    "pocket": "📌",
    "whoop": "💚",
    "apple_health": "❤️",
    "google_maps": "📍",
    "claude_chat": "🤖",
    "chatgpt_chat": "💬",
    "coding_session": "👨‍💻",
}

# Higher weight = prefer this source over others at equal semantic similarity.
# Personal written notes rank highest; passive consumption lowest.
# Canvas is authoritative for course content but sits below personal notes.
SOURCE_WEIGHTS: dict[str, float] = {
    "voice_memo":    1.50,   # personal spoken notes — highest signal (you said it out loud)
    "granola":       1.50,   # personal meeting notes — highest signal
    "apple_notes":   1.45,   # personal notes — very high signal
    "note":          1.45,
    "notion":        1.40,   # curated personal workspace
    "canvas":        1.35,   # authoritative course material
    "file":          1.20,   # manually ingested file — deliberate capture
    "gdrive":        1.20,   # docs you've written
    "kindle":        1.20,   # deliberate reading (highlighted)
    "readwise":      1.20,
    "github":        1.10,
    "photos":        1.10,   # personal memory
    "folder":        1.08,
    "videos":        1.05,
    "pocket":        1.05,   # saved-for-later
    "calendar":      1.00,   # context only — past events not very useful
    "web":           1.00,   # intentionally captured web content
    "gmail":         0.90,   # often boilerplate; noise filter handles the worst
    "youtube":       0.85,
    "youtube_liked": 0.85,
    "podcast":       0.80,
    "twitter":       0.75,
    "spotify":       0.60,   # song lyrics aren't study material
    "instagram":     0.65,
    "tiktok":        0.60,
    "apple_health":  0.80,
    "google_maps":   0.70,
    "claude_chat":   1.30,   # AI conversations reflect deep thinking
    "chatgpt_chat":  1.25,
    "coding_session": 1.15,
}


def _extract_date(meta: dict) -> str:
    """Return a YYYY-MM-DD date string from whichever metadata field is present."""
    for key in ("date", "start_time", "due_at", "watch_date", "date_read", "created_at",
                "created", "last_watched", "saved_date", "published_at", "published",
                "updated_at", "timestamp"):
        val = meta.get(key, "")
        if not val or not isinstance(val, str):
            continue
        v = val.strip()
        # Already YYYY-MM-DD or starts with it
        if len(v) >= 10 and v[4] == "-" and v[7] == "-":
            candidate = v[:10]
            # Sanity-check: year must be reasonable (2000-2035)
            try:
                yr = int(candidate[:4])
                if 2000 <= yr <= 2035:
                    return candidate
            except ValueError:
                pass
    return ""


def _extract_ingest_date(meta: dict) -> str:
    """Return the KB ingest date when available, without falling back to source dates."""
    for key in ("ingested_at", "ingest_date", "added_at"):
        val = meta.get(key, "")
        if not val or not isinstance(val, str):
            continue
        v = val.strip()
        if len(v) >= 10 and v[4] == "-" and v[7] == "-":
            candidate = v[:10]
            try:
                yr = int(candidate[:4])
                if 2000 <= yr <= 2035:
                    return candidate
            except ValueError:
                pass
    return ""


def _extract_recent_activity_date(meta: dict) -> str:
    """Best effort date for recent surfaces: prefer ingest date, then safe source-specific fallbacks."""
    ingest_date = _extract_ingest_date(meta)
    if ingest_date:
        return ingest_date

    source = meta.get("source", "")
    safe_fallback_sources = {
        "note", "apple_notes", "notion", "file", "web", "granola", "gdrive",
        "canvas", "youtube", "readwise", "podcast", "bookmarks",
    }
    if source in safe_fallback_sources:
        return _extract_date(meta)
    return ""


def _normalize_title(title: str) -> str:
    import re as _re
    if not title:
        return ""
    normalized = title.lower().strip()
    normalized = _re.sub(r"\(\d{4}-\d{2}-\d{2}\)", "", normalized)
    normalized = _re.sub(r"[^a-z0-9]+", " ", normalized)
    return _re.sub(r"\s+", " ", normalized).strip()


def _looks_like_schedule_query(query: str) -> bool:
    q = query.lower()
    schedule_terms = (
        "schedule", "calendar", "upcoming", "today", "tomorrow", "this week",
        "next week", "deadline", "due", "meeting", "office hours", "oh", "when is",
        "what time", "what's on", "events", "exam date", "quiz date", "midterm date",
    )
    return any(term in q for term in schedule_terms)


def _calendar_priority(title: str, calendar: str = "") -> int:
    """Lower is better. 0-1 are worth surfacing; 3-4 are noise."""
    text = f"{title} {calendar}".lower()
    high_signal = (
        "exam", "midterm", "final", "quiz", "deadline", "due", "interview",
        "presentation", "application", "hw", "homework", "assignment",
    )
    academic = (
        "operating systems", "computer networks", "financial accounting",
        "analysis of algorithms", "algorithms", "office hours", "section",
        "lecture", "class", "study", "review session",
    )
    personal = (
        "lunch", "dinner", "reservation", "brooklyn", "engagement", "panama",
        "birthday", "wedding", "flight", "trip", "suzanne",
    )
    low_signal = (
        "gym", "open recreation", "closed", "holiday", "candle lighting",
        "shabbat ends", "hebcal", "rangers", "knicks", "yankees", "mets",
        "warriors", "jazz", "pacers", "phillies", "tigers", "braves", "@",
    )
    if any(term in text for term in high_signal):
        return 0
    if any(term in text for term in academic):
        return 1
    if any(term in text for term in personal):
        return 2
    if any(term in text for term in low_signal):
        return 4
    return 3


def _should_hide_calendar_event(title: str, calendar: str = "") -> bool:
    priority = _calendar_priority(title, calendar)
    return priority >= 4


def _should_exclude_recent_item(source: str, title: str) -> bool:
    normalized = _normalize_title(title)
    if source in {"notion", "apple_notes", "note"}:
        low_signal_terms = (
            "to do", "todo", "suggestion box", "test", "test note",
            "feedback", "daily", "book list",
        )
        if any(term in normalized for term in low_signal_terms):
            return True
    if source == "web":
        if any(term in normalized for term in ("earn rewards", "coupon", "promo", "sale")):
            return True
    if source == "gdrive" and normalized in {"untitled document drive", "untitled document"}:
        return True
    return False


def _digest_source_score(meta: dict) -> float:
    """Prefer recent, active, digestible material for the daily briefing."""
    from datetime import date as _date
    source = meta.get("source", "")
    recent_date = _extract_recent_activity_date(meta) or _extract_date(meta)
    days_old = 9999
    if recent_date:
        try:
            days_old = (_date.today() - _date.fromisoformat(recent_date)).days
        except Exception:
            pass

    if source in {"note", "apple_notes", "voice_memo", "granola"}:
        return 2.0 if days_old <= 21 else 1.35
    if source in {"notion", "file", "gdrive", "web"}:
        return 1.65 if days_old <= 21 else 1.2
    if source == "canvas":
        # All canvas content is historical (graduated May 2025) — heavily downrank
        return 0.25
    if source in {"readwise", "kindle", "goodreads", "podcast", "youtube"}:
        return 0.9 if days_old <= 45 else 0.55
    return 0.75


def _digest_item_excluded(meta: dict) -> bool:
    import re as _re_digest_item
    source = (meta.get("source", "") or "").lower().strip()
    title = meta.get("title", "") or ""
    if source in {"calendar", "gmail", "google_calendar", "apple_calendar", "spotify"}:
        return True
    bad_title_patterns = [
        r'\b(exam|midterm|final|quiz|office hours|lecture)\b.*\d{1,2}:\d{2}',
        r'\d{1,2}:\d{2}\s*(am|pm)',
        r'\b(due|deadline)\b.*\d{1,2}/\d{1,2}',
    ]
    if any(_re_digest_item.search(pat, title, _re_digest_item.IGNORECASE) for pat in bad_title_patterns):
        return True
    if _should_exclude_recent_item(source, title):
        return True
    # Old passive course files are usually misleading in the digest.
    if source == "canvas" and _digest_source_score(meta) < 0.5:
        return True
    return False


def _daypart_label(hour: int) -> str:
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 22:
        return "evening"
    return "tonight"


def _query_source_multiplier(query: str, meta: dict) -> float:
    """Adjust ranking by query intent so schedule noise does not dominate knowledge queries."""
    source = meta.get("source", "")
    title = meta.get("title", "")
    if source == "calendar":
        priority = _calendar_priority(title, meta.get("calendar", ""))
        if _looks_like_schedule_query(query):
            if priority == 0:
                return 1.25
            if priority == 1:
                return 1.0
            if priority == 2:
                return 0.85
            return 0.35
        if priority == 0:
            return 0.45
        if priority == 1:
            return 0.18
        return 0.05
    if source == "gmail" and not _looks_like_schedule_query(query):
        return 0.55
    if source in {"spotify", "twitter", "instagram", "tiktok"} and not _looks_like_schedule_query(query):
        return 0.7
    return 1.0


def _recency_weight(meta: dict) -> float:
    """Return a recency multiplier: recent = boost, old = penalty."""
    from datetime import date
    date_str = _extract_date(meta)
    source = meta.get("source", "")
    if not date_str:
        return 0.95
    try:
        d = date.fromisoformat(date_str)
        days = (date.today() - d).days
    except ValueError:
        return 0.95
    # Past calendar events are nearly worthless for search — they already happened
    if source == "calendar" and days > 1:
        return 0.40
    if days < 0:    return 1.20   # future-dated (upcoming) — treat as fresh
    if days < 30:   return 1.20
    if days < 90:   return 1.10
    if days < 180:  return 1.00
    if days < 365:  return 0.90
    if days < 730:  return 0.80
    return 0.70


def _rerank(
    docs: list[str],
    metas: list[dict],
    ids: list[str],
    distances: list[float],
) -> tuple[list[str], list[dict], list[str]]:
    """Re-sort chunks by composite score = cosine_similarity × source_weight × recency_weight."""
    scored = _rerank_scored(docs, metas, ids, distances)
    return (
        [x[1] for x in scored],
        [x[2] for x in scored],
        [x[3] for x in scored],
    )


def _rerank_scored(
    docs: list[str],
    metas: list[dict],
    ids: list[str],
    distances: list[float],
) -> list[tuple[float, str, dict, str]]:
    """Return (score, doc, meta, id) tuples sorted best-first. Used for global dedup across batches."""
    scored = []
    for doc, meta, doc_id, dist in zip(docs, metas, ids, distances):
        sim = max(0.0, 1.0 - dist)
        sw  = SOURCE_WEIGHTS.get(meta.get("source", ""), 1.0)
        rw  = _recency_weight(meta)
        scored.append((sim * sw * rw, doc, meta, doc_id))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _knowledge_level(meta: dict) -> str:
    """Return a short tag describing how well the user likely knows this content.

    Tiers (most to least active engagement):
      WROTE THIS        — user authored it (personal notes, voice memos)
      BUILT THIS        — user coded/implemented it (github)
      ATTENDED          — user was in the meeting (granola)
      EDITED IN NOTION  — user actively curated this
      COURSE MATERIAL   — in their curriculum; may or may not have read in depth
      SAVED / UNREAD    — bookmarked, liked, or queued but may not have engaged with
      FADED             — older material, familiarity likely low
    """
    from datetime import date as _date, timedelta
    today = _date.today().isoformat()
    source = meta.get("source", "")

    # Future Canvas items = not yet in class
    for future_key in ("due_at", "unlock_at", "unlock_date", "available_from"):
        val = meta.get(future_key, "")
        if val and isinstance(val, str) and len(val) >= 10 and val[:10] > today:
            return "NOT YET COVERED IN COURSE"

    # User actively created / built
    if source in ("github",):
        return "BUILT THIS — deep familiarity"
    if source in ("note", "apple_notes", "voice_memo"):
        return "WROTE THIS — personal thinking"
    if source in ("notion",):
        return "EDITED IN NOTION — personal curation"
    if source in ("granola",):
        return "ATTENDED THIS MEETING"

    # Passive saves — bookmarked/queued but engagement unknown
    if source in ("url", "pocket", "youtube", "youtube_liked", "spotify", "readwise"):
        status = meta.get("status", "")
        if status in ("unread", "saved", ""):
            return "SAVED — may not have read/watched in depth"
        return "SAVED / PARTIALLY ENGAGED"

    # Canvas: course material — varies widely in engagement
    if source == "canvas":
        date_str = _extract_date(meta)
        six_months_ago = (_date.today() - timedelta(days=180)).isoformat()
        if date_str and date_str >= six_months_ago:
            return "COURSE MATERIAL — currently in curriculum"
        return "COURSE MATERIAL — from a past course"

    # Everything else: classify by recency
    date_str = _extract_date(meta)
    if not date_str:
        return ""
    six_months_ago = (_date.today() - timedelta(days=180)).isoformat()
    two_years_ago  = (_date.today() - timedelta(days=730)).isoformat()
    if date_str >= six_months_ago:
        return "RECENTLY ENGAGED"
    if date_str >= two_years_ago:
        return "STUDIED PREVIOUSLY — may have faded"
    return "OLDER MATERIAL — likely faded"


def _word_overlap_ratio(a: str, b: str) -> float:
    """Return fraction of words in the shorter string that also appear in the longer one."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _dedup_by_content(
    items: list[tuple],  # (score, doc, meta, id)
    threshold: float = 0.80,
) -> list[tuple]:
    """Remove near-duplicate chunks: if two chunks share > threshold word overlap, keep the longer one."""
    kept: list[tuple] = []
    for item in items:
        doc = item[1]
        duplicate = False
        for i, existing in enumerate(kept):
            existing_doc = existing[1]
            if _word_overlap_ratio(doc, existing_doc) > threshold:
                # Keep the longer chunk (more information)
                if len(doc) > len(existing_doc):
                    kept[i] = item
                duplicate = True
                break
        if not duplicate:
            kept.append(item)
    return kept


def _build_numbered_context(docs: list[str], metas: list[dict]) -> tuple[str, list[dict]]:
    """Build numbered context string with knowledge-level annotations and return source list."""
    parts = []
    sources = []
    for i, (doc, meta) in enumerate(zip(docs, metas), 1):
        title = meta.get("title", meta.get("source", "Unknown"))
        source = meta.get("source", "unknown")
        url = meta.get("url", meta.get("source_url", ""))
        icon = SOURCE_ICONS.get(source, "📌")
        date = _extract_date(meta)
        date_label = f" · {date}" if date else ""
        status = meta.get("status", "")
        status_label = " · UNREAD" if status in ("unread", "saved") else (" · IN PROGRESS" if status == "in_progress" else "")
        knowledge = _knowledge_level(meta)
        knowledge_label = f" · ⚠ {knowledge}" if knowledge else ""
        parts.append(f"[{i}] {icon} {title} (source: {source}{date_label}{status_label}{knowledge_label})\n{doc}")
        sources.append({
            "index": i,
            "title": title,
            "source": source,
            "icon": icon,
            "url": url,
            "full_text": doc,
            "knowledge_level": knowledge,
        })
    return "\n\n---\n\n".join(parts), sources


def _build_grouped_context(docs: list[str], metas: list[dict]) -> tuple[str, list[dict]]:
    """Build context grouped by source type, return source list."""
    by_source: dict[str, list[tuple[str, dict, int]]] = {}
    sources = []
    for i, (doc, meta) in enumerate(zip(docs, metas), 1):
        src = meta.get("source", "unknown")
        by_source.setdefault(src, []).append((doc, meta, i))
        title = meta.get("title", meta.get("source", "Unknown"))
        url = meta.get("url", meta.get("source_url", ""))
        icon = SOURCE_ICONS.get(src, "📌")
        sources.append({
            "index": i,
            "title": title,
            "source": src,
            "icon": icon,
            "url": url,
            "excerpt": doc[:300] + "..." if len(doc) > 300 else doc,
        })

    parts = []
    for src, items in by_source.items():
        icon = SOURCE_ICONS.get(src, "📌")
        src_chunks = "\n\n".join(
            f"[{idx}] [{m.get('title', src)}]" + (f" · {_extract_date(m)}" if _extract_date(m) else "") + f"\n{d}"
            for d, m, idx in items
        )
        parts.append(f"=== {icon} {src.upper()} ===\n{src_chunks}")
    return "\n\n".join(parts), sources


class JimmyEngine:
    def __init__(self):
        self.store = JimmyStore(CHROMA_DIR)
        self._upcoming_cache: dict = {}  # cache_key → (result, timestamp)
        self._anthropic_client = None
        self._openai_client = None

    def _hybrid_search(
        self, query: str, n_candidates: int = 200, shuffle_factor: float = 0.0
    ) -> list[tuple[float, str, dict, str]]:
        """Combine vector + BM25 via Reciprocal Rank Fusion, then apply source/recency weights.

        Returns (composite_score, doc, meta, id) sorted best-first.

        shuffle_factor: 0.0 = pure score ranking; >0 injects random perturbation to surface
        buried content. E.g. 0.2 adds ±20% noise to each score.
        """
        n_candidates = min(n_candidates, self.store.count() or 1)

        # ── Vector search ────────────────────────────────────────────────────
        vec = self.store.search(query, n_results=n_candidates)
        vec_ids   = vec["ids"][0]
        vec_dists = vec["distances"][0]

        # ── BM25 keyword search ──────────────────────────────────────────────
        bm25_hits = self.store.bm25_search(query, n_results=n_candidates)
        bm25_ids  = [h[0] for h in bm25_hits]

        # ── Reciprocal Rank Fusion — vector weighted 2:1 over BM25 ──────────
        # Vector captures semantic intent; BM25 adds recall for exact terms.
        # Weighting 2:1 prevents keyword accidents from displacing intent matches.
        K = 60
        rrf: dict[str, float] = {}
        for rank, doc_id in enumerate(vec_ids):
            rrf[doc_id] = rrf.get(doc_id, 0.0) + 2.0 / (K + rank + 1)
        for rank, doc_id in enumerate(bm25_ids):
            rrf[doc_id] = rrf.get(doc_id, 0.0) + 1.0 / (K + rank + 1)

        # ── Build doc/meta lookup ─────────────────────────────────────────────
        lookup: dict[str, tuple[str, dict]] = {
            doc_id: (doc, meta)
            for doc_id, doc, meta in zip(vec_ids, vec["documents"][0], vec["metadatas"][0])
        }
        bm25_only = [doc_id for doc_id in bm25_ids if doc_id not in lookup]
        if bm25_only:
            try:
                extra = self.store.collection.get(
                    ids=bm25_only, include=["documents", "metadatas"]
                )
                for doc_id, doc, meta in zip(extra["ids"], extra["documents"], extra["metadatas"]):
                    lookup[doc_id] = (doc, meta)
            except Exception:
                pass

        # ── Apply source/recency quality weights on top of RRF ───────────────
        scored: list[tuple[float, str, dict, str]] = []
        for doc_id, rrf_score in rrf.items():
            if doc_id not in lookup:
                continue
            doc, meta = lookup[doc_id]
            sw = SOURCE_WEIGHTS.get(meta.get("source", ""), 1.0)
            rw = _recency_weight(meta)
            qw = _query_source_multiplier(query, meta)
            final_score = rrf_score * sw * rw * qw
            # Serendipity: inject random perturbation so buried gems can surface
            if shuffle_factor > 0.0:
                final_score = final_score * (1.0 + random.uniform(-shuffle_factor, shuffle_factor))
            scored.append((final_score, doc, meta, doc_id))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def _web_search(self, query: str, n_results: int = 3) -> str:
        """Search DuckDuckGo and fetch top results via Jina reader. Returns plain text context."""
        try:
            import re as _re
            # DuckDuckGo HTML search — no API key needed
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }
            with httpx.Client(timeout=4, follow_redirects=True) as client:
                resp = client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query, "kl": "us-en"},
                    headers=headers,
                )
                # Parse result URLs from DDG HTML
                urls = _re.findall(
                    r'<a[^>]+class="result__url"[^>]*>([^<]+)</a>',
                    resp.text
                )
                # Also try the uddg param links
                uddg_links = _re.findall(r'uddg=([^&"]+)', resp.text)
                from urllib.parse import unquote
                parsed_urls = [unquote(u) for u in uddg_links if u.startswith("http")]
                final_urls = [u.strip() for u in parsed_urls if "duckduckgo" not in u]

            if not final_urls:
                return ""

            # Filter out low-quality domains and rank by query word overlap in URL path
            LOW_QUALITY_DOMAINS = {"reddit.com", "quora.com", "pinterest.com"}
            query_words = set(query.lower().split())

            def _url_score(url: str) -> float:
                import re as _re2
                for bad in LOW_QUALITY_DOMAINS:
                    if bad in url:
                        return -1.0  # discard
                path_words = set(_re2.findall(r'[a-z]{3,}', url.lower()))
                overlap = len(query_words & path_words) / max(len(query_words), 1)
                return overlap

            scored_urls = [(u, _url_score(u)) for u in final_urls]
            scored_urls = [(u, s) for u, s in scored_urls if s >= 0]
            scored_urls.sort(key=lambda x: -x[1])
            final_urls = [u for u, _ in scored_urls[:3]]  # hard cap at top 3

            if not final_urls:
                return ""

            # Fetch URLs via Jina reader concurrently (was sequential — up to 30s)
            def _fetch_jina(url: str) -> str:
                try:
                    with httpx.Client(timeout=3, follow_redirects=True) as c:
                        r = c.get(
                            f"https://r.jina.ai/{url}",
                            headers={"Accept": "text/plain", "User-Agent": "Jimmy/1.0"},
                        )
                        if r.status_code == 200:
                            text = r.text[:1200].strip()
                            if text:
                                return f"WEB SOURCE: {url}\n{text}"
                except Exception:
                    pass
                return ""

            snippets = []
            with ThreadPoolExecutor(max_workers=3) as pool:
                for result in pool.map(_fetch_jina, final_urls):
                    if result:
                        snippets.append(result)
            return "\n\n---\n\n".join(snippets)
        except Exception:
            return ""

    @staticmethod
    def _resolve_model(tier: str = "default") -> tuple[str, str]:
        """Return (provider, model_name) for the given tier."""
        from ..config import JIMMY_LLM_PROVIDER, LLM_TIER_MAP
        provider = JIMMY_LLM_PROVIDER
        models = LLM_TIER_MAP.get(provider, LLM_TIER_MAP["ollama"])
        return provider, models.get(tier, models["default"])

    def _ollama_chat(self, prompt: str, model: str, max_tokens: int) -> str:
        from openai import OpenAI
        from ..config import OLLAMA_BASE_URL
        client = OpenAI(
            base_url=OLLAMA_BASE_URL,
            api_key="ollama",
            http_client=httpx.Client(trust_env=False, timeout=httpx.Timeout(120.0, connect=10.0)),
        )
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content

    def _anthropic_chat(self, prompt: str, model: str, max_tokens: int) -> str:
        import anthropic
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not anthropic_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        if self._anthropic_client is None or getattr(self._anthropic_client, '_api_key', None) != anthropic_key:
            self._anthropic_client = anthropic.Anthropic(api_key=anthropic_key)
            self._anthropic_client._api_key = anthropic_key
        msg = self._anthropic_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text

    def _openai_chat(self, prompt: str, model: str, max_tokens: int) -> str:
        from openai import OpenAI
        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not openai_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        if self._openai_client is None:
            self._openai_client = OpenAI(api_key=openai_key)
        response = self._openai_client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content

    def _chat(self, prompt: str, max_tokens: int = 2048, tier: str = "default") -> str:
        """Dispatch to configured LLM provider with fallback chain."""
        provider, model = self._resolve_model(tier)

        # Build fallback order starting with configured provider
        fallback_order = [provider] + [p for p in ["ollama", "anthropic", "openai"] if p != provider]

        for p in fallback_order:
            try:
                m = model if p == provider else _get_tier_model(p, tier)
                if p == "ollama":
                    return self._ollama_chat(prompt, m, max_tokens)
                elif p == "anthropic":
                    return self._anthropic_chat(prompt, m, max_tokens)
                elif p == "openai":
                    return self._openai_chat(prompt, m, max_tokens)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("LLM provider %s failed: %s", p, exc)
                continue
        raise RuntimeError("All LLM providers failed")

    def _should_skip_query_expansion(self, question: str) -> bool:
        """Return True when query expansion adds no value and only costs latency.

        Skip when:
        - The query is short (< 6 words): short queries are already precise.
        - The query contains specific proper nouns / technical terms / quoted phrases:
          these are best matched literally and expansion often dilutes relevance.
        """
        import re
        words = question.split()
        # Too short — already specific
        if len(words) < 6:
            return True
        # Contains a quoted phrase — user wants exact matching
        if '"' in question or "'" in question:
            return True
        # Contains a proper noun (title-case word that isn't sentence-start)
        tokens = re.findall(r'\b[A-Z][a-z]{2,}\b', question)
        # More than one title-case token (beyond first word) → specific enough
        non_first = [t for t in tokens if question.index(t) > 0]
        if len(non_first) >= 2:
            return True
        # Contains year or date
        if re.search(r'\b(19|20)\d{2}\b', question):
            return True
        return False

    def _expand_query(self, question: str) -> list[str]:
        """Generate 3 alternative search angles for a question via a fast LLM call.

        Returns [original_question, alt1, alt2, alt3].  Falls back to [question] on failure.
        Uses claude-haiku for speed — this runs before every search.
        Results are cached for 24h by question hash.
        """
        import json, re
        # Skip LLM for short/simple queries — not worth the latency (~500ms saved)
        if self._should_skip_query_expansion(question):
            return [question]

        # Check cache first (saves ~500ms per repeated/similar query)
        cache_key = hashlib.md5(question.lower().strip().encode()).hexdigest()
        cached = _QUERY_CACHE.get(cache_key)
        if cached and (_time.time() - cached[1]) < _QUERY_CACHE_TTL:
            return cached[0]

        raw = self._chat(
            f"Generate 3 semantically different reformulations of this question for searching a personal knowledge base "
            f"(notes, emails, calendar, Canvas LMS, meetings, etc.).\n"
            f"Each reformulation must approach the question from a DIFFERENT semantic angle — "
            f"vary the terminology, perspective, and framing so they surface different relevant documents.\n"
            f"Reformulation 1: rephrase using domain-specific terminology or synonyms.\n"
            f"Reformulation 2: focus on related entities, people, or context (not the direct question).\n"
            f"Reformulation 3: reframe as what source material would say (e.g. a lecture note, meeting summary, or highlight).\n"
            f"Question: {question}\n"
            f"Output ONLY a JSON array of 3 strings, nothing else.",
            max_tokens=200,
            tier="fast",
        )
        m = re.search(r'\[[\s\S]*?\]', raw)
        if m:
            try:
                alts = [q for q in json.loads(m.group(0)) if isinstance(q, str)][:3]
                result = [question] + alts
                _QUERY_CACHE[cache_key] = (result, _time.time())
                return result
            except Exception:
                pass
        return [question]

    def _multi_search(self, queries: list[str], n_candidates: int = 200) -> list[tuple]:
        """Run hybrid search for each query, merge by best score per doc, return sorted list."""
        best: dict[str, tuple] = {}
        for q in queries:
            for score, doc, meta, doc_id in self._hybrid_search(q, n_candidates=n_candidates):
                if doc_id not in best or score > best[doc_id][0]:
                    best[doc_id] = (score, doc, meta, doc_id)
        return sorted(best.values(), key=lambda x: x[0], reverse=True)

    def get_upcoming_exams(self, days: int = 14) -> list[dict]:
        """Return upcoming exam events from calendar within the next N days.

        Returns list of dicts: {title, date, days_until, topic_guess}
        Sorted by date ascending (soonest first).
        """
        from datetime import date as _date
        today = _date.today().isoformat()
        EXAM_KEYWORDS = {"exam", "midterm", "final", "quiz", "test"}
        seen_exam_keys: set[tuple[str, str]] = set()
        try:
            upcoming_data = self.upcoming(days=days)
        except Exception:
            return []
        results = []
        for event in upcoming_data.get("events", []):
            title = event.get("title", "")
            normalized_title = _normalize_title(title)
            if "study for" in normalized_title or "review" in normalized_title:
                continue
            if any(kw in normalized_title for kw in EXAM_KEYWORDS):
                event_date = event.get("date", "")
                dedup_key = (event_date, normalized_title)
                if dedup_key in seen_exam_keys:
                    continue
                seen_exam_keys.add(dedup_key)
                try:
                    days_until = (_date.fromisoformat(event_date) - _date.today()).days
                except Exception:
                    days_until = 0
                # Guess study topic from exam title
                tl = normalized_title
                if "os" in tl or "operating" in tl:
                    topic = "operating systems"
                elif "network" in tl:
                    topic = "computer networks"
                elif "algorithm" in tl or "algo" in tl:
                    topic = "algorithms"
                elif "account" in tl:
                    topic = "financial accounting"
                else:
                    import re as _re
                    clean = _re.sub(r'[^\w\s]', '', title)
                    clean = _re.sub(r'\b(exam|midterm|final|quiz|test|study|for)\b', '', clean, flags=_re.IGNORECASE)
                    topic = clean.strip() or title
                results.append({
                    "title": title,
                    "date": event_date,
                    "days_until": days_until,
                    "topic_guess": topic,
                })
        results.sort(key=lambda x: x["date"])
        return results

    def _upcoming_summary(self, days: int = 14) -> str:
        """Return a compact upcoming calendar summary for injecting into ask() context.
        Cached for 5 minutes to avoid re-fetching on every ask() call."""
        import time
        cache_key = f"upcoming_{days}"
        now = time.time()
        if cache_key in self._upcoming_cache:
            result, ts = self._upcoming_cache[cache_key]
            if now - ts < 300:  # 5 min TTL
                return result
        result = self._compute_upcoming_summary(days)
        self._upcoming_cache[cache_key] = (result, now)
        return result

    def _compute_upcoming_summary(self, days: int = 14) -> str:
        from datetime import date as _date, timedelta
        today = _date.today().isoformat()
        cutoff = (_date.today() + timedelta(days=days)).isoformat()
        try:
            all_data = self.store.collection.get(
                where={"source": "calendar"}, include=["documents", "metadatas"]
            )
        except Exception:
            return ""
        seen: list[tuple[str, str]] = []
        events: list[tuple[str, str, int]] = []  # (date, title, priority)
        IMPORTANT_KEYWORDS = {"exam", "midterm", "final", "quiz", "test", "due", "deadline", "interview", "meeting", "presentation"}
        for meta in (all_data.get("metadatas") or []):
            date_str = _extract_date(meta)
            if not date_str or date_str < today or date_str > cutoff:
                continue
            title = meta.get("title", "")
            calendar = meta.get("calendar", "")
            if _should_hide_calendar_event(title, calendar):
                continue
            normalized = _normalize_title(title)
            if any(existing_date == date_str and _word_overlap_ratio(normalized, existing_title) >= 0.8 for existing_date, existing_title in seen):
                continue
            seen.append((date_str, normalized))
            # Priority: 0 = high (exams/deadlines), 1 = normal
            priority = 0 if any(kw in title.lower() for kw in IMPORTANT_KEYWORDS) else _calendar_priority(title, calendar)
            events.append((date_str, title, priority))
        # Sort by date, then by priority (important first within same date)
        events.sort(key=lambda x: (x[0], x[2]))
        # Keep the summary compact and useful.
        high = [(d, t) for d, t, p in events if p == 0]
        academic = [(d, t) for d, t, p in events if p == 1]
        personal = [(d, t) for d, t, p in events if p == 2]
        events_final = high[:20] + academic[:12] + personal[:6]
        events_final.sort()  # re-sort by date
        if not events_final:
            return ""
        lines = [f"UPCOMING CALENDAR (next {days} days, today={today}):"]
        cur = None
        for date_str, title in events_final:
            if date_str != cur:
                cur = date_str
                try:
                    from datetime import date as _d
                    _dt = _d.fromisoformat(date_str)
                    lbl = _dt.strftime("%A %b ") + str(_dt.day)
                except Exception:
                    lbl = date_str
                lines.append(f"  {lbl}:")
            lines.append(f"    - {title}")
        return "\n".join(lines)

    def ask(self, question: str, n_results: int = 10) -> dict:
        from datetime import datetime
        _now = datetime.now()
        today = _now.strftime("%A, %B ") + str(_now.day) + _now.strftime(", %Y")

        queries = self._expand_query(question)
        scored = self._multi_search(queries, n_candidates=50)

        # Dedup by title (fast, eliminates exact same-document chunks)
        seen_title_keys: set[str] = set()
        deduped: list[tuple] = []
        for item in scored:
            meta = item[2]
            src = meta.get("source", "")
            title = meta.get("title", "")
            key = f"{src}::{title}"
            if key in seen_title_keys:
                continue
            seen_title_keys.add(key)
            deduped.append(item)

        # Content-similarity dedup: remove near-duplicate chunks (>80% word overlap)
        deduped = _dedup_by_content(deduped, threshold=0.80)

        scored = deduped[:n_results]

        if not scored:
            return {"answer": "Nothing relevant found. Try ingesting more content first.", "sources": [], "question": question}

        docs  = [x[1] for x in scored]
        metas = [x[2] for x in scored]

        context, sources = _build_numbered_context(docs, metas)

        # Always inject a compact upcoming calendar block so the LLM can answer
        # time-sensitive questions ("due this week", "what's on my schedule") accurately
        upcoming_block = self._upcoming_summary(days=14)
        upcoming_section = f"\n\n{upcoming_block}" if upcoming_block else ""

        # Build a compact "Sources:" line for the end of the response
        source_names = ", ".join(
            s["title"] or s["source"]
            for s in sources[:10]
            if s.get("title") or s.get("source")
        )

        # Classify whether context is dominated by passive sources
        passive_sources = {"url", "pocket", "youtube", "youtube_liked", "spotify", "readwise", "canvas"}
        active_sources = {"note", "apple_notes", "voice_memo", "notion", "github", "granola"}
        n_passive = sum(1 for m in metas if m.get("source", "") in passive_sources)
        n_active  = sum(1 for m in metas if m.get("source", "") in active_sources)
        mostly_passive = n_passive > n_active and n_passive >= 3

        # If the question looks like an explanation request and context is mostly passive,
        # supplement with a web search so Jimmy can actually teach
        web_context = ""
        learning_keywords = ("explain", "how does", "what is", "teach me", "help me understand",
                              "why does", "tell me about", "learn", "what are", "how do",
                              "more about", "yes", "sure", "go ahead", "understand")
        is_learning = any(kw in question.lower() for kw in learning_keywords)
        has_passive_sources = (n_passive / max(len(metas), 1)) > 0.5
        seems_like_learning = is_learning or has_passive_sources
        if mostly_passive or (seems_like_learning and n_active == 0):
            web_context = self._web_search(question)

        web_section = f"\n\nWEB SEARCH RESULTS — top 3 external sources (each labeled WEB SOURCE: with URL). Use to explain/teach; always attribute as 'From the web:':\n{web_context}" if web_context else ""

        answer = self._chat(
            f"You are Jimmy — a second brain built from {JIMMY_USER_NAME}'s actual notes, meetings, courses, and work.\n"
            f"{_user_prompt_context()}\n"
            f"Today is {today}.{upcoming_section}\n\n"
            f"CRITICAL LANGUAGE RULES — follow these EXACTLY based on source label:\n\n"
            f"- If a source is labeled 'WROTE THIS' or 'BUILT THIS' or 'EDITED IN NOTION' or 'ATTENDED THIS MEETING' — "
            f"reference it as something he knows well: 'In your notes on X...', 'you wrote that...', 'in your meeting with...'\n"
            f"- If a source is labeled 'COURSE MATERIAL' — NEVER assume he absorbed it deeply. Teach it to him: "
            f"'Your [course name] material explains X as...' or 'There's a reading in your OS course that covers Y...' "
            f"Then offer: 'Want me to go deeper on this?'\n"
            f"- If a source is labeled 'SAVED' or is marked UNREAD/SAVED — "
            f"NEVER say 'you know' or 'you read'. Say: 'You saved a [article/video/paper] called [title] that covers X...' "
            f"Offer: 'Want me to walk you through the key ideas?'\n"
            f"- If a source is labeled 'STUDIED PREVIOUSLY' or 'OLDER MATERIAL' — "
            f"assume partial recall. Say 'you studied this before — it covered...', 'you may remember from your notes...'\n"
            f"- WEB SEARCH RESULTS: label clearly as 'From the web:' before any web-sourced info.\n\n"
            f"NEVER say 'based on your knowledge base' or 'in your second brain' — be specific about the actual source.\n"
            f"NEVER say 'As an AI...' or mention being an AI.\n\n"
            f"CITATIONS: Use [1], [2], [3] markers inline when referencing a specific source so the UI can hyperlink them.\n\n"
            f"RESPONSE FORMAT:\n"
            f"- Lead with the direct answer in 1-2 sentences — no preamble.\n"
            f"- Use **bold** for key terms and concepts.\n"
            f"- Prefer 3-4 tight paragraphs over 10 bullet points. Only use bullets for genuinely list-like content (steps, lists of algorithms, etc.).\n"
            f"- Use code blocks (```language) for any code, pseudocode, or command syntax.\n"
            f"- Cite sources inline with [N] markers when referencing specific content.\n"
            f"- For exam-related questions: add a 'Key takeaway:' line at the end with the one thing to remember.\n"
            f"- Use short ## headers only if the answer spans genuinely distinct sub-topics.\n"
            f"- End your response with exactly this line:\n"
            f"  Sources: {source_names}\n\n"
            f"STRICT RULES:\n"
            f"- NEVER say 'as you know' or 'you know that' for course/saved sources — he may not know it.\n"
            f"- NEVER assume a Canvas reading was read in depth unless his own notes reference it.\n"
            f"- If sources are thin, say so and lean on web search results to teach.\n"
            f"- Write in second person ('you', 'your') — conversational, direct, like a smart TA.\n"
            f"- NEVER infer habits or routines from individual data points.\n"
            f"- For OS/Networks/Algorithms/Accounting questions: be precise and technical — this is exam prep.\n\n"
            f"SOURCES:\n{context}{web_section}\n\n"
            f"QUESTION: {question}",
            max_tokens=1024,
        )
        return {"answer": answer, "sources": sources, "question": question}

    def context_pack(self, topic: str, n_results: int = 30) -> dict:
        queries = self._expand_query(topic)
        scored = self._multi_search(queries, n_candidates=50)[:n_results]
        docs  = [x[1] for x in scored]
        metas = [x[2] for x in scored]

        if not docs:
            return {"context_pack": f"Nothing found about '{topic}'.", "sources": [], "topic": topic}

        context, sources = _build_numbered_context(docs, metas)
        pack = self._chat(
            f"You are Jimmy. Build a comprehensive personal briefing on \"{topic}\" from this person's actual knowledge.\n\n"
            f"Go deep — pull every relevant detail from the sources. Quote directly where it adds value.\n\n"
            f"## What I Know\nEverything relevant they've written, learned, or noted [N]. Be exhaustive and specific.\n\n"
            f"## Key People & Context\nEvery relevant person, project, decision, or deadline — with detail.\n\n"
            f"## How This Connects\nConnections to other areas of their knowledge. What does this tie into?\n\n"
            f"## What's Unresolved\nOpen questions, tensions, or things they kept returning to without resolution.\n\n"
            f"SOURCES:\n{context}",
            max_tokens=4000,
            tier="deep",
        )
        return {"context_pack": pack, "sources": sources, "topic": topic}

    def resurface(self, topic: str, n_results: int = 20) -> dict:
        from datetime import date as _date, timedelta as _td
        queries = self._expand_query(topic)

        # Run hybrid search with serendipity — 15% shuffle to avoid always returning the same items
        scored_all = []
        for q in queries:
            for item in self._hybrid_search(q, n_candidates=50, shuffle_factor=0.15):
                scored_all.append(item)

        # Merge by best score per doc
        best: dict[str, tuple] = {}
        for item in scored_all:
            doc_id = item[3]
            if doc_id not in best or item[0] > best[doc_id][0]:
                best[doc_id] = item
        scored = sorted(best.values(), key=lambda x: x[0], reverse=True)

        # Prefer content from 6+ months ago — invert recency so forgotten things surface
        six_months_ago = (_date.today() - _td(days=180)).isoformat()
        old_items = [x for x in scored if _extract_date(x[2]) and _extract_date(x[2]) <= six_months_ago]
        recent_items = [x for x in scored if x not in old_items]

        # Build final pool: up to 60% old content, rest recent — then cap at n_results
        n_old = min(len(old_items), max(1, int(n_results * 0.6)))
        n_recent = n_results - n_old
        # Random sample from old items so we don't always get the same ones
        sampled_old = random.sample(old_items, min(n_old, len(old_items)))
        final = sampled_old + recent_items[:n_recent]
        # Re-sort by score for coherent presentation
        final.sort(key=lambda x: x[0], reverse=True)
        final = final[:n_results]

        docs  = [x[1] for x in final]
        metas = [x[2] for x in final]

        if not docs:
            return {"result": f"Nothing found related to '{topic}'.", "sources": [], "topic": topic}

        context, sources = _build_numbered_context(docs, metas)
        result = self._chat(
            f"You are Jimmy — {JIMMY_USER_NAME}'s second brain.\n\n"
            f"They're thinking about \"{topic}\". Surface the most useful, forgotten, and surprising things "
            f"from his notes that connect to this. Prioritize things from 6+ months ago that he may have forgotten.\n\n"
            f"FORMAT:\n"
            f"- Start with the single most relevant thing he noted — quote or closely paraphrase it, name the source and date.\n"
            f"- Then surface 2-4 additional pieces he may have forgotten: prior notes, meetings, highlights, or observations. "
            f"For each, say roughly when it was and what it connects to.\n"
            f"- End with one provocative question that this resurfaced material raises for him right now.\n\n"
            f"RULES:\n"
            f"- Write in second person: 'You noted this...', 'Three months ago you wrote...', 'Your meeting with X covered...'\n"
            f"- Be specific — names, dates, direct quotes from the sources\n"
            f"- For items not accessed in 30+ days, frame as: 'You noted this X months ago — still relevant?'\n"
            f"- For items from 6+ months ago, open with: 'Hey, remember when you learned/wrote/noted this?'\n"
            f"- Show patterns across sources: what keeps recurring?\n"
            f"- Do not invent — only surface what is explicitly in the sources\n\n"
            f"SOURCES:\n{context}",
            max_tokens=2000,
        )
        return {"result": result, "sources": sources, "topic": topic}

    def connections(self, topic: str, n_results: int = 20) -> dict:
        queries = self._expand_query(topic)
        scored = self._multi_search(queries, n_candidates=50)[:n_results]
        docs  = [x[1] for x in scored]
        metas = [x[2] for x in scored]

        if not docs:
            return {"result": f"Nothing found related to '{topic}'.", "sources": [], "topic": topic}

        context, sources = _build_grouped_context(docs, metas)
        result = self._chat(
            f"You are Jimmy. Show how \"{topic}\" threads through this person's knowledge base.\n\n"
            f"## Where It Shows Up\nSpecific places this appears — courses, meetings, notes, work [N].\n\n"
            f"## Common Threads\nIdeas that repeat across multiple sources.\n\n"
            f"## Tensions\nWhere sources disagree, contradict, or show changing views.\n\n"
            f"## The Bigger Picture\nWhat does the pattern across all sources reveal?\n\n"
            f"SOURCES (grouped by type):\n{context}"
        )
        return {"result": result, "sources": sources, "topic": topic}

    def digest(self, sample_size: int = 30) -> dict:
        """Daily briefing — uses targeted searches instead of full collection scan."""
        from datetime import datetime, timedelta
        import json
        from pathlib import Path
        from zoneinfo import ZoneInfo

        # Check pre-compiled cache first (from daemon compile_daily)
        cache_path = JIMMY_DATA_DIR / "digest_cache.json"
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
                if datetime.now() - cached_at < timedelta(minutes=60):
                    return cached
            except Exception:
                pass

        _now = datetime.now(ZoneInfo("America/New_York"))
        today = _now.strftime("%A, %B ") + str(_now.day) + _now.strftime(", %Y")
        daypart = _daypart_label(_now.hour)

        if self.store.count() == 0:
            return {"result": "Knowledge base is empty.", "sources": [], "topic": "digest"}

        seed_queries = [
            "idea concept theory framework insight argument",
            "book reading highlight lesson learned",
            "article essay research finding conclusion",
            "podcast lecture talk explanation definition",
            "connects relates similar parallel pattern contrast",
            "history philosophy ethics politics economics religion Torah",
            "technology science software engineering AI machine learning",
            "business strategy product market startup",
            "project work engineering system design personal notes goals",
        ]
        # Run all seed queries in parallel — use vector-only search to avoid BM25 rebuild hang
        def _vec_search(query: str, n: int = 40):
            """Vector-only search for digest — avoids BM25 rebuild blocking thread pool."""
            try:
                res = self.store.search(query, n_results=n)
                out = []
                for doc_id, dist, doc, meta in zip(
                    res["ids"][0], res["distances"][0],
                    res["documents"][0], res["metadatas"][0]
                ):
                    score = max(0.0, 1.0 - dist)
                    out.append((score, doc, meta, doc_id))
                return out
            except Exception:
                return []

        best: dict[str, tuple] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(_vec_search, q, 40): q
                for q in seed_queries
            }
            for future in as_completed(futures):
                try:
                    for score, doc, meta, doc_id in future.result():
                        if doc_id not in best or score > best[doc_id][0]:
                            best[doc_id] = (score, doc, meta, doc_id)
                except Exception:
                    pass

        filtered = [item for item in best.values() if not _digest_item_excluded(item[2])]

        def _read_cache(name: str) -> dict:
            p = JIMMY_DATA_DIR / name
            if not p.exists():
                return {}
            try:
                data = json.loads(p.read_text())
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

        study_plan = _read_cache("study_plan_cache.json")
        news_summary = _read_cache("news_summary_cache.json")
        recs_cache = _read_cache("recs_cache.json")
        today_focus = study_plan.get("today_focus", "")
        today_topics_list = (study_plan.get("today_topics") or [])[:3]
        active_focus_terms = {t.lower() for t in today_topics_list if t}

        def _focus_bonus(item: tuple) -> float:
            doc = item[1].lower()
            meta = item[2]
            title = (meta.get("title", "") or "").lower()
            hay = f"{title} {doc[:300]}"
            if not active_focus_terms:
                return 1.0
            if any(term in hay for term in active_focus_terms):
                return 1.35
            source = meta.get("source", "")
            if source == "canvas":
                return 0.3  # Historical course material — suppress
            return 0.9

        sorted_items = sorted(
            filtered,
            key=lambda x: x[0] * _recency_weight(x[2]) * _digest_source_score(x[2]) * _focus_bonus(x),
            reverse=True,
        )[:sample_size]
        all_docs  = [x[1] for x in sorted_items]
        all_metas = [x[2] for x in sorted_items]

        context, sources = _build_numbered_context(all_docs, all_metas)
        today_topics = ", ".join(today_topics_list)
        exam_names = ", ".join(ex.get("name", "") for ex in (study_plan.get("exams") or [])[:2] if ex.get("name"))
        news_text = (news_summary.get("summary") or "").strip()
        if news_text:
            news_text = news_text[:700]
        media_recs = recs_cache.get("media_recommendations") or []
        media_lines = []
        for rec in media_recs[:3]:
            title = rec.get("title", "")
            kind = rec.get("type", "")
            why = rec.get("why", "")
            if title and kind:
                media_lines.append(f"- {kind}: {title} — {why[:180]}")
        media_block = "\n".join(media_lines)

        raw = self._chat(
            f"You are Jimmy writing {JIMMY_USER_NAME}'s {daypart} briefing. Today is {today}.\n"
            f"{_user_prompt_context()}. {JIMMY_USER_NAME} graduated from Columbia in May 2025 and now works at Datadog as a software engineer. "
            f"He learns best when things are explained slowly, clearly, and from first principles. "
            f"Any course material (Operating Systems, Algorithms, Financial Accounting, Computer Networks, etc.) is HISTORICAL — from college, NOT current work. "
            f"Do NOT reference old courses as if they are ongoing. His current focus is professional work, personal projects, and self-directed learning.\n\n"
            f"Below are excerpts from his knowledge base.\n\n"
            f"CURRENT CONTEXT:\n"
            f"- Today's focus: {today_focus or 'None'}\n"
            f"- Topics on deck: {today_topics or 'None'}\n\n"
            f"NEWS CONTEXT:\n{news_text or 'No fresh news summary available.'}\n\n"
            f"OPTIONAL MEDIA RECOMMENDATIONS:\n{media_block or '- none'}\n\n"
            f"Write a daily briefing that feels current, grounded, and easy to absorb.\n\n"
            f"## What Feels Most Current\n"
            f"2-3 sentences on what he has actually been touching recently. Prefer recent notes, recent files, recent notion pages, recent drive docs, and recent current-semester course material. "
            f"Do NOT surface old material just because it is intellectually rich.\n\n"
            f"## What To Understand Today\n"
            f"Pick 1-2 concepts that matter right now and explain them in plain language. "
            f"Teach gently: remind him what the term means, why it matters, and what intuition to hold onto. "
            f"If the source is a class file or reading, say that the material covers the concept; do not imply he already knows it.\n\n"
            f"## One Useful Connection\n"
            f"Make one concrete cross-domain connection only if it is easy to follow in 2 sentences. "
            f"Skip this entirely if the connection would feel forced or too abstract.\n\n"
            f"## Coming Up\n"
            f"In 1-2 sentences, mention what you are working on or what is on deck professionally or personally. Do NOT reference old college courses.\n\n"
            f"## News In Brief\n"
            f"In 1-2 sentences, summarize only the most relevant news from the NEWS CONTEXT. Keep it digestible.\n\n"
            f"## One Next Step\n"
            f"End with exactly one concrete next step for today: one topic to review, one note to revisit, or one question to answer.\n\n"
            f"## One Thing To Explore Later\n"
            f"If the OPTIONAL MEDIA RECOMMENDATIONS are relevant, mention exactly one book, podcast, or video in one sentence and why it fits right now. If not relevant, omit this section entirely.\n\n"
            f"ABSOLUTE RULES — NEVER VIOLATE:\n"
            f"- NEVER mention calendar events, exam dates, assignment deadlines, or scheduled meetings by name\n"
            f"- NEVER use bullet points\n"
            f"- NEVER include raw URLs in the output\n"
            f"- Keep each section to 2-3 sentences maximum\n"
            f"- NO email subject lines or email content of any kind\n"
            f"- NO emojis whatsoever — not a single character\n"
            f"- NO phrases like 'Based on your knowledge base' or 'According to your notes'\n"
            f"- Avoid high-abstraction jargon unless you immediately explain it simply\n"
            f"- If something seems older than the recent period, leave it out unless it clearly connects to something current\n"
            f"- Write in clean, warm prose paragraphs\n\n"
            f"STRICT RULES:\n"
            f"- Under 420 words total\n"
            f"- Open with the most current thing, not the most intellectually impressive thing\n"
            f"- NEVER start with 'Good morning', 'Good afternoon', 'Good evening', or any greeting/header line\n"
            f"- Write in second person throughout\n"
            f"- For passive course material, use language like 'Your OS material covers...' or 'There's a class file on...'\n"
            f"- For personal notes, use language like 'You wrote...' or 'In your notes...'\n"
            f"- NO inline citations like [1]\n"
            f"- Grounded entirely in the sources below — do not invent\n\n"
            f"KNOWLEDGE SOURCES:\n{context}",
            max_tokens=1500,
        )
        # Post-processing: aggressive cleanup of the raw LLM output
        import re as _re
        text = raw.strip()
        text = _re.sub(r'\r\n', '\n', text)
        text = _re.sub(r'\n{3,}', '\n\n', text)
        # Strip emojis (using module-level compiled pattern — avoids recompilation)
        text = _EMOJI_PATTERN.sub('', text)
        # Remove lines containing calendar-like patterns that slipped through
        lines = text.split('\n')
        lines = [l for l in lines if not _re.search(
            r'\b(exam|quiz|deadline|assignment|due|meeting|lecture|class|office hours)\b',
            l, _re.IGNORECASE
        )]
        # Collapse multiple blank lines
        text = _re.sub(r'\n{3,}', '\n\n', '\n'.join(lines))
        # Remove trailing whitespace from each line
        text = '\n'.join(l.rstrip() for l in text.split('\n'))
        text = _re.sub(r'^##\s*Good (morning|afternoon|evening|night|tonight)[^\n]*\n*', '', text, flags=_re.IGNORECASE)
        text = _re.sub(r'^Good (morning|afternoon|evening|night|tonight)[^\n]*\n*', '', text, flags=_re.IGNORECASE)
        if active_focus_terms and "distributed systems" not in active_focus_terms:
            text = _re.sub(r'[^.\n]*distributed systems[^.\n]*\.\s*', '', text, flags=_re.IGNORECASE)
        text = text.strip()
        return {"result": text, "sources": sources, "topic": "digest"}

    def daily_extras(self) -> dict:
        """Generate a personalized fun fact and vocabulary word from the knowledge base."""
        from datetime import datetime
        import random

        if self.store.count() == 0:
            return {"fact": None, "vocab": None}

        # Sample a diverse cross-section of the KB for fact generation
        fact_queries = [
            "surprising unexpected counterintuitive discovery",
            "origin history etymology roots",
            "statistics percentage proportion rate",
            "invented discovered created founded",
            "paradox contradiction irony strange",
            "ancient medieval historical civilization",
            "scientific finding experiment result",
            "philosophical thought experiment argument",
        ]
        # Run all fact and vocab queries in parallel
        all_queries = fact_queries + [
            "term definition concept theory principle",
            "named after called known as referred to",
            "technical jargon discipline field domain",
            "Greek Latin root derived from means",
            "phenomenon effect law theorem conjecture",
        ]
        vocab_queries_set = set(all_queries[len(fact_queries):])

        best_fact: dict = {}
        best_vocab: dict = {}

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(self._hybrid_search, q, 40): q
                for q in all_queries
            }
            for future in as_completed(futures):
                q = futures[future]
                try:
                    results = future.result()
                    target = best_vocab if q in vocab_queries_set else best_fact
                    for score, doc, meta, doc_id in results:
                        if doc_id not in target or score > target[doc_id][0]:
                            target[doc_id] = (score, doc, meta, doc_id)
                except Exception:
                    pass

        fact_items = sorted(best_fact.values(), key=lambda x: x[0], reverse=True)[:20]
        fact_docs = [x[1] for x in fact_items]
        fact_context = "\n\n".join(f"[{i+1}] {d[:400]}" for i, d in enumerate(fact_docs))

        vocab_items = sorted(best_vocab.values(), key=lambda x: x[0], reverse=True)[:20]
        vocab_docs = [x[1] for x in vocab_items]
        vocab_context = "\n\n".join(f"[{i+1}] {d[:400]}" for i, d in enumerate(vocab_docs))

        today = datetime.now().strftime("%A, %B %d")

        # Generate fact
        RALPH_CONTEXT = f"{_user_prompt_context()} {JIMMY_USER_CONTEXT}"

        fact_raw = self._chat(
            f"You are a curious tutor. Today is {today}.\n\n"
            f"{RALPH_CONTEXT}\n\n"
            f"Based on the excerpts below from his knowledge base, surface ONE genuinely interesting "
            f"fact or insight that he probably hasn't consciously noticed or synthesized yet. "
            f"Prioritize topics from his active courses (OS, Networks, Algorithms) or his Datadog prep "
            f"(query engines: Arrow, Trino, ClickHouse, Calcite) or his Jewish/Torah interests.\n\n"
            f"Rules:\n"
            f"- 2–3 sentences max. No filler. No 'Did you know?'\n"
            f"- Ground it in the sources — don't invent\n"
            f"- Make it specific (names, numbers, places) not vague\n"
            f"- Never reference calendar events, emails, or meeting titles\n"
            f"- No bullet points, no citations like [1]\n"
            f"- Return ONLY the fact text, nothing else\n\n"
            f"SOURCES:\n{fact_context}",
            max_tokens=200,
        )

        # Generate vocab word — prioritize CS/tech terms relevant to Datadog prep or current courses
        vocab_raw = self._chat(
            f"You are a vocabulary tutor. Today is {today}.\n\n"
            f"{RALPH_CONTEXT}\n\n"
            f"Based on the excerpts below from his knowledge base, choose ONE interesting word "
            f"that appears in or is directly relevant to what he is studying. "
            f"Prioritize CS/tech terms relevant to his Datadog prep (query engines, distributed systems, "
            f"columnar storage) or his current courses (OS, Networks, Algorithms). "
            f"Also consider Torah/Hebrew terms if a strong one appears.\n\n"
            f"Return a JSON object with exactly these fields (no markdown, no extra text):\n"
            f'{{"word": "...", "pronunciation": "...", "part_of_speech": "...", '
            f'"definition": "...", "etymology": "...", "example": "..."}}\n\n'
            f"Rules:\n"
            f"- definition: one clear sentence\n"
            f"- etymology: origin language + root meaning, 1 sentence\n"
            f"- example: a sentence using the word in context of his studies or Datadog prep\n"
            f"- No padding\n\n"
            f"SOURCES:\n{vocab_context}",
            max_tokens=300,
        )

        # Generate personalized motivational note
        motivational_raw = self._chat(
            f"Write a single motivational sentence (1 sentence only, no more) for {JIMMY_USER_NAME}.\n"
            f"{RALPH_CONTEXT}\n"
            f"Today is {today}. Make it specific to his situation — Datadog prep, Columbia finals, "
            f"or his Jewish values. Keep it genuine, not cheesy. "
            f"Return ONLY the sentence, nothing else.",
            max_tokens=80,
            tier="fast",
        )

        import json as _json
        vocab = None
        try:
            # Strip any accidental markdown fences
            clean = vocab_raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            vocab = _json.loads(clean)
        except Exception:
            pass

        return {
            "fact": fact_raw.strip() if fact_raw else None,
            "vocab": vocab,
            "motivational_note": motivational_raw.strip() if motivational_raw else None,
        }

    def build_topic_graph(self) -> dict:
        """Two-pass topic graph: nodes first, then grounded edges. Cache to ~/.jimmy/graph_cache.json."""
        import json
        import re
        from datetime import datetime, timezone
        from pathlib import Path

        count = self.store.count()
        if count == 0:
            return {"nodes": [], "edges": [], "built_at": datetime.now(timezone.utc).isoformat()}

        seed_queries = [
            # Academic / learning
            "courses classes lectures homework assignments exams grades",
            "computer science programming algorithms code software engineering",
            "math statistics data science machine learning AI",
            "history political science economics social science humanities",
            "biology chemistry physics science lab oceanography",
            "writing essays papers research thesis projects",
            # People & social
            "friends people relationships family social hangout",
            "professors teachers mentors advisors colleagues",
            "clubs organizations extracurricular activities campus",
            # Work & career
            "internship job career work experience professional",
            "side project startup building creating product",
            "goals plans ambitions dreams future aspirations",
            # Media & culture
            "music albums artists songs concerts playlists spotify liked",
            "movies films shows watching TV streaming rated review",
            "books reading highlights kindle authors literature",
            "podcasts episodes shows audio listened",
            "youtube videos content creators watched",
            "video games gaming played enjoying",
            # Places
            "Columbia University New York City Manhattan campus dorm",
            "places travel visited cities countries neighborhoods",
            "restaurants food eating cooking recipes",
            # Knowledge & ideas
            "ideas concepts theories frameworks mental models",
            "technology tools apps software products reviews",
            "health fitness wellness exercise sports",
            "finance money investing economics personal finance",
            "philosophy ethics psychology behavior",
            # Personal
            "memories experiences personal life reflections",
            "notes thoughts observations insights journaling",
            "meetings conversations discussions decisions granola",
            "emails correspondence threads gmail",
            "recent activity this week this month today",
        ]

        # Exclude calendar events — they're schedule noise, not knowledge topics
        GRAPH_EXCLUDE_SOURCES = {"calendar"}

        best: dict[str, tuple] = {}  # id → (score, doc, meta, id)
        for query in seed_queries:
            results = self.store.search(query, n_results=40)
            for score, doc, meta, doc_id in _rerank_scored(
                results["documents"][0], results["metadatas"][0],
                results["ids"][0], results["distances"][0],
            ):
                if meta.get("source") in GRAPH_EXCLUDE_SOURCES:
                    continue
                if doc_id not in best or score > best[doc_id][0]:
                    best[doc_id] = (score, doc, meta, doc_id)

        sorted_items = sorted(best.values(), key=lambda x: x[0], reverse=True)
        all_docs  = [x[1] for x in sorted_items[:200]]
        all_metas = [x[2] for x in sorted_items[:200]]

        source_types = list({m.get("source", "unknown") for m in all_metas})
        context_parts = []
        for i, (doc, meta) in enumerate(zip(all_docs, all_metas)):
            title = meta.get("title", meta.get("source", "Unknown"))
            source = meta.get("source", "unknown")
            context_parts.append(f"[{i}] [{source}] {title}\n{doc[:250]}")
        context = "\n\n---\n\n".join(context_parts)

        from datetime import date as _date
        today = _date.today().isoformat()

        # ── Pass 1: Extract nodes only ───────────────────────────────────────
        node_prompt = (
            f"You are analyzing someone's personal knowledge base (sources: {', '.join(source_types)}).\n"
            f"Today is {today}. Source chunks include dates where available (shown as · YYYY-MM-DD).\n"
            f"Extract 15-20 specific topic nodes representing the MOST prominent topics in this knowledge base.\n\n"
            f"Return ONLY a valid JSON array (no markdown, no explanation):\n"
            f'[{{"id": "snake_case_id", "label": "Human Label", "category": "learning|work|people|projects|media|external", "size": 1-5, "summary": "1 sentence about this in the KB"}}]\n\n'
            f"Rules:\n"
            f"- Be SPECIFIC: \"Oasis\" not \"Music\", \"Prof. Smith\" not \"Professor\", \"ECON 1105\" not \"Economics\"\n"
            f"- Only include topics with clear evidence in the sources — do not invent\n"
            f"- MERGE near-duplicates: 'Columbia' and 'Columbia University' = one node\n"
            f"- size = prominence (5=central topic with many mentions, 1=minor mention); weight recent activity more heavily\n"
            f"- DO NOT conflate different time periods — a high school mention is different from a current one\n"
            f"- Prefer quality over quantity: 15 precise nodes > 35 noisy ones\n"
            f"- categories: learning (courses/skills/knowledge), work (jobs/tasks/career), people (individuals), projects (things being built), media (shows/books/music/film), external (news/world events/places)\n\n"
            f"KNOWLEDGE BASE ({len(all_docs)} chunks):\n{context}"
        )
        raw_nodes = self._chat(node_prompt, max_tokens=4000, tier="deep")
        arr_match = re.search(r'\[[\s\S]*\]', raw_nodes)
        if not arr_match:
            raise ValueError("Node extraction did not return a JSON array")
        nodes: list[dict] = json.loads(arr_match.group(0))

        # ── Anchor each node to actual chunk IDs (fast vector lookups) ───────
        for node in nodes:
            r = self.store.search(node["label"], n_results=20)
            node["source_chunk_ids"] = r["ids"][0] if r["ids"][0] else []

        # ── Pass 2: Extract edges with strict co-occurrence grounding ────────
        node_ids_str = ", ".join(f'"{n["id"]}"' for n in nodes)
        node_list_str = "\n".join(f'- {n["id"]}: {n["label"]}' for n in nodes)
        edge_prompt = (
            f"Given the following topic nodes and the same knowledge base chunks, identify meaningful edges.\n\n"
            f"VALID NODE IDs:\n{node_list_str}\n\n"
            f"STRICT RULES — read carefully:\n"
            f"- ONLY add an edge if you can cite a specific chunk (by [N]) where BOTH topics appear together\n"
            f"- Do NOT add edges based on general world knowledge or because topics seem related in real life\n"
            f"- Do NOT add an edge if only one topic is mentioned and the other is implied\n"
            f"- Aim for 15-25 high-confidence edges — fewer strong edges is better than many weak ones\n\n"
            f"Return ONLY a valid JSON array (no markdown):\n"
            f'[{{"source": "node_id", "target": "node_id", "label": "brief relationship phrase"}}]\n\n'
            f"KNOWLEDGE BASE ({len(all_docs)} chunks):\n{context}"
        )
        raw_edges = self._chat(edge_prompt, max_tokens=3000, tier="deep")
        arr_match = re.search(r'\[[\s\S]*\]', raw_edges)
        edges: list[dict] = json.loads(arr_match.group(0)) if arr_match else []

        # Filter edges to valid node IDs only
        valid_ids = {n["id"] for n in nodes}
        edges = [
            e for e in edges
            if e.get("source") in valid_ids and e.get("target") in valid_ids
            and e.get("source") != e.get("target")
        ]

        graph = {
            "nodes": nodes,
            "edges": edges,
            "built_at": datetime.now(timezone.utc).isoformat(),
        }

        cache_path = JIMMY_DATA_DIR / "graph_cache.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(graph, indent=2))

        return graph

    def topic_summary(self, label: str, category: str = "", source_chunk_ids: list[str] | None = None) -> dict:
        """Search KB for a topic and return an AI-written summary + source cards.

        source_chunk_ids: chunk IDs anchored to this node during graph build — fetched
        directly (bypassing semantic search) as the highest-confidence starting point.
        """
        # ── Fetch anchored chunks directly by ID (known-relevant) ────────────
        anchor_docs: list[str] = []
        anchor_metas: list[dict] = []
        anchor_id_set: set[str] = set()
        if source_chunk_ids:
            try:
                r = self.store.collection.get(
                    ids=source_chunk_ids,
                    include=["documents", "metadatas"],
                )
                anchor_docs = r["documents"]
                anchor_metas = r["metadatas"]
                anchor_id_set = set(source_chunk_ids)
            except Exception:
                pass

        # ── Multi-query hybrid search — cast a wide net ─────────────────────
        # Use multiple query formulations to surface more relevant chunks
        base_query = f"{label} {category}".strip()
        search_queries = [
            base_query,
            label,  # label alone
            f"notes about {label}",
            f"course {label} lecture",
        ]
        seen_ids: set[str] = set(anchor_id_set)
        all_docs: list[str] = list(anchor_docs)
        all_metas: list[dict] = list(anchor_metas)

        for q in search_queries:
            for _score, doc, meta, doc_id in self._hybrid_search(q, n_candidates=120):
                if doc_id not in seen_ids:
                    seen_ids.add(doc_id)
                    all_docs.append(doc)
                    all_metas.append(meta)
                    if len(all_docs) >= 300:
                        break
            if len(all_docs) >= 300:
                break

        if not all_docs:
            return {"summary": f"No information found about '{label}' in your knowledge base.", "sources": []}

        # Deduplicate by title to get diverse sources, then pick top 60
        seen_titles: set[str] = set()
        deduped_docs, deduped_metas = [], []
        for doc, meta in zip(all_docs, all_metas):
            t = meta.get("title", "")
            if t not in seen_titles:
                seen_titles.add(t)
                deduped_docs.append(doc)
                deduped_metas.append(meta)

        use_docs = deduped_docs[:60]
        use_metas = deduped_metas[:60]

        context, sources = _build_numbered_context(use_docs, use_metas)
        n = len(use_docs)

        source_cards = [
            {
                "title": s["title"],
                "source": s["source"],
                "icon": s["icon"],
                "url": s.get("url", ""),
                "excerpt": s["full_text"][:300] + "..." if len(s["full_text"]) > 300 else s["full_text"],
                "full_text": s["full_text"],
            }
            for s in sources[:8]
        ]

        from datetime import date as _date
        today = _date.today().isoformat()
        unique_titles = len(seen_titles)
        prompt = (
            f'You are Jimmy. Today is {today}. You found {unique_titles} unique documents and {n} relevant chunks about "{label}". '
            f'Write a concise, factual summary of what is in the knowledge base about this topic.\n\n'
            f'REQUIREMENTS:\n'
            f'- Focus on KNOWLEDGE and INFORMATION — concepts, facts, projects, courses, ideas\n'
            f'- Name specific titles, courses, projects, concepts, and dates where relevant\n'
            f'- Write 3-5 sentences. Be informative and neutral in tone.\n'
            f'- Write in second person ("Your notes cover...", "You have material on...")\n'
            f'- Distinguish recent vs older material when dates make it clear\n'
            f'- Sources marked · UNREAD or · SAVED: not yet consumed — say "saved" not "read"\n'
            f'- NEVER psychoanalyze, infer emotional patterns, characterize relationships, or draw conclusions about personal habits or mental state\n'
            f'- NEVER quote private or sensitive content — paraphrase the topic only\n'
            f'- If sources are personal/private (journal entries, private notes about people), describe the topic neutrally: e.g. "You have personal notes on this topic" — no details\n'
            f'- If you see course material, name the specific concepts or algorithms covered\n'
            f'- If you see project work, name the project and what it involves\n\n'
            f'SOURCES ({n} chunks from {unique_titles} unique documents):\n{context}'
        )
        summary = self._chat(prompt, max_tokens=1200, tier="default")
        return {"summary": summary, "sources": source_cards}

    def learn(self, topic: str) -> dict:
        """Generate a Duolingo-style lesson on a topic from the KB."""
        import json, re

        queries = self._expand_query(topic)
        scored = self._multi_search(queries, n_candidates=50)[:40]
        docs = [x[1] for x in scored]
        metas = [x[2] for x in scored]

        if not docs:
            return {"error": f"Nothing found about '{topic}' in your knowledge base."}

        context, sources = _build_numbered_context(docs, metas)

        raw = self._chat(
            f"""You are the world's best teacher. Generate a Duolingo-style lesson on "{topic}" using ONLY facts from the knowledge base below.

Rules:
- Assume the learner knows NOTHING about this topic
- Every sentence must be clear to a smart 16-year-old
- Use concrete analogies to everyday things (food, sports, cities, money)
- No jargon without an immediate plain-English explanation
- Keep each card SHORT — concept bodies max 3 sentences, MC questions crisp
- Make the hook genuinely surprising or counterintuitive
- MC distractors must be plausible — common misconceptions, not obviously wrong
- true_false statements should be non-obvious
- All content must come from the sources below — do not invent facts

Output a single valid JSON object (no markdown fences, no trailing commas):
{{
  "topic": "{topic}",
  "tagline": "one punchy sentence",
  "emoji": "appropriate emoji",
  "lesson_plan": [
    {{"type":"intro","title":"...","hook":"...","body":"...","emoji":"🚀"}},
    {{"type":"concept","title":"...","body":"...","key_point":"...","analogy":"...","emoji":"💡"}},
    {{"type":"concept","title":"...","body":"...","key_point":"...","analogy":"...","emoji":"🔧"}},
    {{"type":"multiple_choice","question":"...","options":["A) ...","B) ...","C) ...","D) ..."],"answer":"B","explanation":"...","emoji":"❓"}},
    {{"type":"true_false","statement":"...","answer":true,"explanation":"...","emoji":"🤔"}},
    {{"type":"concept","title":"...","body":"...","key_point":"...","analogy":"...","emoji":"⚡"}},
    {{"type":"multiple_choice","question":"...","options":["A) ...","B) ...","C) ...","D) ..."],"answer":"A","explanation":"...","emoji":"🎯"}},
    {{"type":"summary","title":"You got it.","recap":["...","...","..."],"emoji":"🎉"}}
  ]
}}

KNOWLEDGE BASE:
{context}""",
            max_tokens=3000,
            tier="default",
        )

        # Parse JSON — try to extract from raw if needed
        m = re.search(r'\{[\s\S]*\}', raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"error": "Failed to parse lesson. Try again.", "raw": raw[:500]}

    def practice(self, topic: str, n_results: int = 20, difficulty: str = "medium") -> dict:
        """Generate mixed practice exercises from the user's actual notes and courses on a topic.

        Prioritizes Canvas course materials for academic topics. Injects upcoming exam context
        so questions feel like real exam prep when exams are near.
        """
        import json, re
        from datetime import date as _date
        today = _date.today().isoformat()

        # --- Prioritize course material sources for academic topics ---
        # Run multiple targeted searches to get diverse, relevant content
        ACADEMIC_TOPICS = {
            "operating systems", "os", "computer networks", "networks", "networking",
            "algorithms", "algorithm", "data structures", "financial accounting",
            "accounting", "distributed systems", "computer science",
        }
        topic_lower = topic.lower()
        is_academic = any(t in topic_lower for t in ACADEMIC_TOPICS)

        # Current course codes for Spring 2026
        CURRENT_COURSE_CODES = {
            "coms4118", "4118",         # OS
            "csee4119", "4119",         # Networks
            "csor4231", "4231",         # Algorithms
            "busigu4013", "4013",       # Financial Accounting
        }
        CURRENT_COURSE_KEYWORDS = {
            "operating system", "os midterm", "coms4118",
            "computer network", "networks midterm", "csee4119",
            "algorithms", "csor4231",
            "financial accounting", "accounting midterm", "busigu4013",
        }

        queries = self._expand_query(topic)
        scored_all = self._multi_search(queries, n_candidates=300)

        # Separate course/note material from other sources
        course_sources = {"canvas", "note", "apple_notes", "notion", "file", "gdrive"}
        course_scored = [x for x in scored_all if x[2].get("source") in course_sources]
        other_scored = [x for x in scored_all if x[2].get("source") not in course_sources]

        # For academic topics: heavily prefer current course material
        if is_academic and course_scored:
            # Prioritize canvas chunks from current courses by boosting their scores
            def _is_current_course(meta: dict) -> bool:
                title = (meta.get("title") or "").lower()
                course = (meta.get("course") or "").lower()
                combined = title + " " + course
                return any(code in combined for code in CURRENT_COURSE_CODES) or \
                       any(kw in combined for kw in CURRENT_COURSE_KEYWORDS)

            current_course_scored = [x for x in course_scored if _is_current_course(x[2])]
            other_course_scored = [x for x in course_scored if not _is_current_course(x[2])]

            if current_course_scored:
                # Heavily prioritize current course material: up to 12 from current + 4 other + 4 misc
                scored = (current_course_scored[:12] + other_course_scored[:4] + other_scored[:4])[:n_results]
            else:
                # Fall back to all course material
                scored = (course_scored[:15] + other_scored[:5])[:n_results]
        else:
            scored = scored_all[:n_results]

        docs  = [x[1] for x in scored]
        metas = [x[2] for x in scored]

        if not docs:
            return {"exercises": [], "topic": topic,
                    "message": f"Nothing found about '{topic}' in your knowledge base. Try ingesting some courses or notes on this topic first."}

        context, sources = _build_numbered_context(docs, metas)

        # --- Inject upcoming exam awareness ---
        exam_context = ""
        try:
            upcoming_block = self._upcoming_summary(days=7)
            if upcoming_block:
                EXAM_KWS = {"exam", "midterm", "final", "quiz", "test"}
                if any(kw in upcoming_block.lower() for kw in EXAM_KWS):
                    exam_context = f"\n\nURGENT CONTEXT:\n{upcoming_block}\nGenerate questions that would appear on these exams."
        except Exception:
            pass

        # --- Build difficulty-specific instructions ---
        if difficulty == "easy":
            diff_note = "Focus on definitions, core concepts, and basic mechanisms. Questions should build confidence."
            mix_note = (
                "3 MULTIPLE CHOICE (definitions, basic facts) + "
                "2 CONCEPT (explain a term or mechanism) + "
                "1 APPLICATION (simple scenario)"
            )
        elif difficulty == "hard":
            diff_note = "Focus on edge cases, tradeoffs, algorithm correctness, and synthesis across topics. Exam-level difficulty."
            mix_note = (
                "2 MULTIPLE CHOICE (tricky edge cases, common exam traps) + "
                "2 APPLICATION (complex scenarios requiring judgment) + "
                "1 SYNTHESIS (connect two concepts or compare two approaches) + "
                "1 hard CONCEPT or CODING challenge"
            )
        else:  # medium (default)
            diff_note = "Mix of core concepts and application. Should feel like a real exam warm-up."
            mix_note = (
                "2 MULTIPLE CHOICE (one easy definition, one tricky application) + "
                "1 CONCEPT (explain a mechanism in depth) + "
                "2 APPLICATION (concrete scenario) + "
                "1 SYNTHESIS (connect two ideas from the sources)"
            )

        raw = self._chat(
            f'You are Jimmy — {JIMMY_USER_NAME}\'s personal exam tutor. Today is {today}.{exam_context}\n\n'
            f'{JIMMY_USER_NAME} is preparing for "{topic}" at difficulty: {difficulty.upper()}.\n'
            f'{diff_note}\n\n'
            f'Based ONLY on his actual course materials and notes below, generate exactly 6 exam-quality exercises.\n\n'
            f'INTERLEAVING RULE: Mix topic types — do NOT ask 3 questions about the same sub-concept in a row. '
            f'Alternate between different aspects of {topic} (e.g., OS: scheduling → memory → synchronization → file systems → scheduling again). '
            f'Research shows interleaving improves retention vs blocked practice.\n\n'
            f'DESIRABLE DIFFICULTY: Make questions one level harder than the student thinks they need. '
            f'The goal is productive struggle, not easy wins.\n\n'
            f'EXERCISE MIX:\n{mix_note}\n\n'
            f'WHAT MAKES A GOOD QUESTION HERE:\n'
            f'- For OS: focus on process states, virtual memory (page tables, TLB, demand paging), scheduling algorithms (Round Robin, SJF, MLFQ), synchronization (mutex, semaphore, monitors, deadlock conditions), file systems (inodes, journaling)\n'
            f'- For Networks: focus on TCP vs UDP tradeoffs, congestion control (AIMD, slow start), DNS resolution, HTTP/HTTPS, routing protocols (BGP, OSPF), the 4-layer model and what happens at each layer\n'
            f'- For Algorithms: focus on dynamic programming recurrences, NP-completeness reductions, greedy correctness proofs, graph algorithms (Dijkstra, Bellman-Ford, MST), amortized analysis\n'
            f'- For Accounting: focus on journal entries, T-accounts, balance sheet equation, revenue recognition, matching principle, depreciation methods\n\n'
            f'REQUIREMENTS FOR ALL QUESTIONS:\n'
            f'- Draw from SPECIFIC content in the sources — name the actual algorithm, concept, or principle\n'
            f'- Questions test understanding of mechanisms and tradeoffs, not just definitions\n'
            f'- "answer": complete and educational — something worth reading even if you knew the answer\n'
            f'- "explanation": 2-4 sentences teaching the concept, referencing the specific source material. Include the "why."\n'
            f'- "source_hint": "From your [specific title] in [Canvas/notes/etc.]"\n\n'
            f'FOR MULTIPLE CHOICE questions:\n'
            f'  "question": "stem\\nA) option1\\nB) option2\\nC) option3\\nD) option4"\n'
            f'  "answer": "B) [full correct answer text]"\n'
            f'  "options": ["A) option1","B) option2","C) option3","D) option4"]\n'
            f'  Distractors must be plausible — use real misconceptions from this topic (e.g. confusing mutex with semaphore, or TCP with UDP guarantees).\n\n'
            f'Return ONLY valid JSON array (no markdown fences, no trailing comma):\n'
            f'[{{"type":"multiple_choice|concept|application|synthesis|coding",'
            f'"question":"...","difficulty":"easy|medium|hard","answer":"...",'
            f'"explanation":"...","source_hint":"...","options":null_or_array_of_4_strings}}]\n\n'
            f'SOURCES (prioritizing course materials):\n{context}',
            max_tokens=4000,
        )
        m = re.search(r'\[[\s\S]*\]', raw)
        exercises = []
        if m:
            try:
                exercises = [e for e in json.loads(m.group(0)) if isinstance(e, dict)]
            except Exception:
                pass
        # Ensure options field exists on all exercises
        for ex in exercises:
            if "options" not in ex:
                ex["options"] = None
        return {"exercises": exercises, "topic": topic, "sources": sources, "difficulty": difficulty}

    def evaluate_answer(self, question: str, user_answer: str, correct_answer: str,
                        explanation: str, topic: str) -> dict:
        """Evaluate a user's practice answer and give specific, gap-identifying feedback."""
        # Detect MCQ: correct answer starts with A/B/C/D)
        import re as _re
        is_mcq = bool(_re.match(r'^[A-D]\)', correct_answer.strip()))
        if is_mcq:
            # For MCQ, grade by exact option match — no LLM needed
            user_letter = user_answer.strip()[:2].upper()
            correct_letter = correct_answer.strip()[:2].upper()
            is_correct = user_letter == correct_letter or user_answer.strip().upper() == correct_answer.strip().upper()
            score = "correct" if is_correct else "incorrect"
            feedback = (
                f"Correct! {explanation}" if is_correct
                else f"The correct answer was {correct_answer}. {explanation}"
            )
            return {
                "score": score,
                "feedback": feedback,
                "key_gap": None if is_correct else f"Review: {explanation}",
                "follow_up": None,
            }

        result = self._chat(
            f'You are a rigorous but encouraging tutor grading a practice answer on "{topic}".\n\n'
            f'QUESTION: {question}\n\n'
            f'THEIR ANSWER: {user_answer}\n\n'
            f'CORRECT ANSWER: {correct_answer}\n\n'
            f'EXPLANATION: {explanation}\n\n'
            f'Grade strictly on correctness — not effort or length.\n\n'
            f'SCORING RUBRIC:\n'
            f'- "correct": all key concepts are present and accurate\n'
            f'- "partial": the core idea is right but a significant detail, mechanism, or nuance is wrong or missing\n'
            f'- "incorrect": the answer is wrong, confused, or misses the main point entirely\n\n'
            f'Give feedback in this exact JSON format (no markdown fences):\n'
            f'{{"score":"correct|partial|incorrect",'
            f'"feedback":"2-3 sentences — acknowledge what was right, then pinpoint the specific error or gap. '
            f'Quote or reference the correct answer directly. Name the exact concept or mechanism they misunderstood.",'
            f'"key_gap":"Name the precise concept, definition, or reasoning step they are missing — be specific enough '
            f'that they know exactly what to review. For example: \'You conflated X with Y — X means... whereas Y means...\'. '
            f'Set to null only if score is correct.",'
            f'"follow_up":"A targeted follow-up question that directly tests the gap you identified, not a generic deepening question"}}\n\n'
            f'Do not be vague. "Missing detail" is not useful — name the detail.',
            max_tokens=600,
            tier="fast",
        )
        import json, re
        m = re.search(r'\{[\s\S]*\}', result)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"score": "partial", "feedback": result, "key_gap": None, "follow_up": None}

    def spark(self, days_recent: int = 14, days_old: int = 60) -> dict:
        """Find cross-domain connections using semantic search to pre-match pairs, then LLM to articulate the insight."""
        import json, re, random
        from collections import defaultdict
        from datetime import date as _date, datetime as _datetime, timedelta

        # Check pre-compiled cache first (from daemon compile_daily)
        cache_path = JIMMY_DATA_DIR / "sparks_cache.json"
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                cached_at = _datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
                if _datetime.now() - cached_at < timedelta(hours=6):
                    return cached
            except Exception:
                pass

        today = _date.today().isoformat()
        cutoff_recent = (_date.today() - timedelta(days=days_recent)).isoformat()
        cutoff_old    = (_date.today() - timedelta(days=days_old)).isoformat()
        SPARK_EXCLUDE = {"calendar", "gmail", "google_calendar"}
        # High-signal sources: prefer content user actively studied/read/wrote
        HIGH_SIGNAL_SOURCES = {
            "canvas", "apple_notes", "note", "granola", "kindle", "readwise",
            "notion", "pocket", "youtube", "podcast", "file", "gdrive",
            "github", "twitter",
        }
        # Diversity seed queries: ensure spark searches span all domains, not just recent CS/tech
        SPARK_DIVERSITY_SEEDS = [
            "Torah parasha weekly learning Jewish wisdom halacha",
            "Israel news geopolitics Middle East current events",
            "startup founder entrepreneurship business idea",
            "philosophy ethics meaning life wisdom",
            "music art culture creativity",
            "health fitness wellness habit",
            "book reading insight quote highlight",
            "history economics politics society culture",
            "sports fitness health personal experience",
        ]

        def _domain_key(meta: dict) -> str:
            """Group by source + course so same-course chunks cluster together."""
            source = meta.get("source", "unknown")
            course = meta.get("course_name", meta.get("course_code", ""))
            if course:
                return f"{source}::{course[:50]}"
            title = meta.get("title", "")
            prefix = " ".join(title.split()[:4])
            return f"{source}::{prefix}" if prefix else source

        def _source_priority(meta: dict) -> int:
            """Lower = better for recent pool selection."""
            return 0 if meta.get("source") in HIGH_SIGNAL_SOURCES else 1

        # Metadata-only scan (much faster than loading all documents) to build date buckets
        try:
            all_meta = self.store.collection.get(include=["metadatas"])
        except Exception:
            return {"sparks": [], "message": "Knowledge base is empty."}

        # Bucket item IDs into recent vs old, grouped by domain
        recent_by_domain: dict[str, list[tuple[dict, str]]] = defaultdict(list)
        old_domain_by_id: dict[str, str] = {}

        for meta, doc_id in zip(all_meta["metadatas"], all_meta["ids"]):
            if meta.get("source") in SPARK_EXCLUDE:
                continue
            date_str = _extract_date(meta)
            key = _domain_key(meta)
            if date_str and date_str >= cutoff_recent:
                recent_by_domain[key].append((meta, doc_id))
            elif not date_str or date_str <= cutoff_old:
                old_domain_by_id[doc_id] = key

        if not recent_by_domain:
            # Fallback: widen the window — treat anything without a date or within 90 days as "recent"
            _wider_cutoff = (_date.today() - timedelta(days=90)).isoformat()
            for meta, doc_id in zip(all_meta["metadatas"], all_meta["ids"]):
                if meta.get("source") in SPARK_EXCLUDE:
                    continue
                date_str = _extract_date(meta)
                key = _domain_key(meta)
                if not date_str or date_str >= _wider_cutoff:
                    recent_by_domain[key].append((meta, doc_id))
            if not recent_by_domain:
                return {"sparks": [], "message": f"No content found from the last {days_recent} days. Try syncing sources or ingesting something new."}
        if not old_domain_by_id:
            # Fallback: treat older recent items as "old" for comparison
            _older_cutoff = (_date.today() - timedelta(days=30)).isoformat()
            for meta, doc_id in zip(all_meta["metadatas"], all_meta["ids"]):
                if meta.get("source") in SPARK_EXCLUDE:
                    continue
                date_str = _extract_date(meta)
                if date_str and date_str < _older_cutoff:
                    key = _domain_key(meta)
                    old_domain_by_id[doc_id] = key
                elif not date_str:
                    key = _domain_key(meta)
                    old_domain_by_id[doc_id] = key
            if not old_domain_by_id:
                return {"sparks": [], "message": "Not enough historical content to find connections. Keep using Jimmy and check back later."}

        # Sort domains: prefer high-signal sources first, then shuffle within priority tiers
        def _domain_priority(domain_key: str) -> int:
            items = recent_by_domain[domain_key]
            return 0 if any(m.get("source") in HIGH_SIGNAL_SOURCES for m, _ in items) else 1

        r_domains = list(recent_by_domain.keys())
        r_domains.sort(key=_domain_priority)
        # Shuffle within each priority group
        high = [d for d in r_domains if _domain_priority(d) == 0]
        low  = [d for d in r_domains if _domain_priority(d) == 1]
        random.shuffle(high)
        random.shuffle(low)
        r_domains = (high + low)[:16]

        # For each domain, pick the item from the highest-signal source
        recent_metas_ids = []
        for domain in r_domains:
            items = recent_by_domain[domain]
            best_item = min(items, key=lambda x: _source_priority(x[0]))
            recent_metas_ids.append(best_item)

        # Build old_by_id dict for fast lookup
        old_by_id: dict[str, tuple[str, dict]] = {}  # id -> (doc, meta) — filled lazily below

        # Fetch actual documents only for the selected recent items
        selected_recent_ids = [doc_id for _, doc_id in recent_metas_ids]
        recent_sample = []
        if selected_recent_ids:
            try:
                recent_docs_result = self.store.collection.get(
                    ids=selected_recent_ids, include=["documents", "metadatas"]
                )
                recent_sample = list(zip(
                    recent_docs_result["documents"],
                    recent_docs_result["metadatas"],
                    recent_docs_result["ids"],
                ))
            except Exception:
                # Fallback: use semantic search to get recent-like content instead of by-ID fetch
                try:
                    fb_res = self.store.search(
                        "operating systems networks algorithms accounting recent study",
                        n_results=min(16, len(selected_recent_ids) + 8)
                    )
                    seen_fb: set[str] = set()
                    for fb_doc, fb_meta, fb_id in zip(
                        fb_res["documents"][0], fb_res["metadatas"][0], fb_res["ids"][0]
                    ):
                        if fb_id not in seen_fb and fb_meta.get("source") not in SPARK_EXCLUDE:
                            seen_fb.add(fb_id)
                            recent_sample.append((fb_doc, fb_meta, fb_id))
                except Exception:
                    pass
        if not recent_sample:
            # Last-resort fallback: pull any random chunks from high-signal sources
            try:
                any_res = self.store.collection.get(
                    include=["documents", "metadatas"],
                    limit=50,
                )
                import random as _rand_spark
                paired_any = list(zip(any_res["documents"], any_res["metadatas"], any_res["ids"]))
                _rand_spark.shuffle(paired_any)
                for fb_doc, fb_meta, fb_id in paired_any:
                    if fb_meta.get("source") not in SPARK_EXCLUDE:
                        recent_sample.append((fb_doc, fb_meta, fb_id))
                    if len(recent_sample) >= 16:
                        break
            except Exception:
                pass
        if not recent_sample:
            return {"sparks": [], "message": "Could not fetch documents for spark. Try syncing sources."}

        # Extract abstract principles from recent content — searching with raw text
        # finds same-topic content; abstract principles find cross-domain connections.
        recent_snippets = "\n".join([
            f"[{i+1}] source={m.get('source','')} | {m.get('title','')[:60]}\n{d[:280]}"
            for i, (d, m, _) in enumerate(recent_sample[:8])
        ])
        raw_themes = self._chat(
            f"These are {min(len(recent_sample), 8)} items someone recently studied or read.\n"
            f"Identify 7 abstract principles or patterns — domain-neutral ideas that could appear in any field.\n"
            f"Think CONCEPTUAL ESSENCE, not topic. Good examples:\n"
            f"  'feedback loops create self-correcting stability'\n"
            f"  'information asymmetry enables exploitation'\n"
            f"  'local rules produce emergent global patterns'\n"
            f"  'tension between efficiency and resilience'\n"
            f"For each, write a search query (≤12 words) that surfaces this idea in ANY domain.\n"
            f"Return ONLY valid JSON: [{{\"theme\": \"abstract principle\", \"query\": \"search query\", \"item_idx\": 1}}]\n\n"
            f"RECENT ITEMS:\n{recent_snippets}",
            max_tokens=500,
            tier="fast",
        )
        themes: list[dict] = []
        m_t = re.search(r'\[[\s\S]*?\]', raw_themes)
        if m_t:
            try:
                themes = [t for t in json.loads(m_t.group(0)) if isinstance(t, dict) and t.get("query")]
            except Exception:
                pass
        # Fallback: use raw doc chunks if theme extraction fails
        if not themes:
            themes = [
                {"theme": "", "query": d[:300], "item_idx": i + 1}
                for i, (d, m, _) in enumerate(recent_sample[:6])
            ]

        # Append diversity seeds — always search for Torah/Israel/philosophy/etc. to prevent
        # sparks from skewing entirely toward CS/technical topics when those dominate recent activity
        diversity_theme_extras = [
            {"theme": seed, "query": seed, "item_idx": 1}
            for seed in SPARK_DIVERSITY_SEEDS
        ]
        themes = themes + diversity_theme_extras

        # Search old KB with abstract queries in parallel — each query is independent
        candidate_pairs: list[tuple] = []
        seen_old_domains: set[str] = set()

        def _search_theme(theme_obj: dict) -> tuple | None:
            """Search old KB for one theme; return best cross-domain pair or None."""
            idx = min(max(int(theme_obj.get("item_idx", 1)) - 1, 0), len(recent_sample) - 1)
            r_doc, r_meta, r_id = recent_sample[idx]
            r_domain = _domain_key(r_meta)
            r_course = r_meta.get("course_name", r_meta.get("course_code", ""))
            search_query = theme_obj.get("query") or r_doc[:300]
            try:
                results = self.store.search(search_query, n_results=40)
            except Exception:
                return None
            for cand_id, cand_doc, cand_meta in zip(
                results["ids"][0], results["documents"][0], results["metadatas"][0]
            ):
                if cand_id not in old_domain_by_id:
                    continue
                if cand_meta.get("source") in SPARK_EXCLUDE:
                    continue
                cand_domain = old_domain_by_id[cand_id]
                if cand_domain == r_domain:
                    continue
                cand_course = cand_meta.get("course_name", cand_meta.get("course_code", ""))
                if r_course and cand_course and r_course == cand_course:
                    continue
                return (r_doc, r_meta, cand_doc, cand_meta, theme_obj.get("theme", ""), cand_domain)
            return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            theme_futures = {
                pool.submit(_search_theme, t): t
                for t in themes[:8]
            }
            for future in as_completed(theme_futures):
                result = future.result()
                if result is None:
                    continue
                r_doc, r_meta, cand_doc, cand_meta, theme, cand_domain = result
                if cand_domain in seen_old_domains:
                    continue
                seen_old_domains.add(cand_domain)
                candidate_pairs.append((r_doc, r_meta, cand_doc, cand_meta, theme))

        if not candidate_pairs:
            return {"sparks": [], "message": "No cross-domain connections found yet. Keep building your knowledge base."}

        # Format pairs for Claude with the abstract theme as a framing hint
        def _label(meta: dict, tag: str) -> str:
            src = meta.get("source", "")
            course = meta.get("course_name", meta.get("course_code", ""))
            date = _extract_date(meta) or tag.lower()
            loc = f"{src} / {course}" if course else src
            return f"[{tag} · {date} · {loc}]"

        def _engagement(meta: dict) -> str:
            """Short label for how actively the user engaged with this content."""
            src = meta.get("source", "")
            if src in ("note", "apple_notes", "voice_memo"):
                return "YOU WROTE:"
            if src == "notion":
                return "YOU EDITED:"
            if src == "github":
                return "YOU BUILT:"
            if src == "granola":
                return "YOU ATTENDED:"
            if src == "canvas":
                return "COURSE MATERIAL:"
            if src in ("url", "pocket", "youtube", "youtube_liked", "spotify", "readwise", "gdrive"):
                return "YOU SAVED:"
            return "YOU SAVED:"

        pairs_ctx = "\n\n".join(
            f"PAIR {i+1}:\n"
            f"  ABSTRACT THEME: {theme or '(semantic match)'}\n"
            f"  RECENT {_label(r_m, 'RECENT')} | ENGAGEMENT: {_engagement(r_m)}\n"
            f"  Title: {r_m.get('title', '')}\n"
            f"  \"{r_d[:350]}\"\n\n"
            f"  PAST   {_label(o_m, 'PAST')} | ENGAGEMENT: {_engagement(o_m)}\n"
            f"  Title: {o_m.get('title', '')}\n"
            f"  \"{o_d[:350]}\""
            for i, (r_d, r_m, o_d, o_m, theme) in enumerate(candidate_pairs)
        )

        raw = self._chat(
            f"You are finding surprising, delightful connections between things in {JIMMY_USER_NAME}'s life.\n"
            f"Today is {today}. {_user_prompt_context()} {JIMMY_USER_CONTEXT}\n\n"
            f"These pairs were pre-matched on a shared abstract principle. Your job: articulate the specific, surprising insight as if you're a brilliant friend who just noticed something no one else would.\n\n"
            f"WHAT A GREAT SPARK LOOKS LIKE:\n"
            f"The title should make {JIMMY_USER_NAME} stop scrolling and think 'wait, what?' Examples of the tone:\n"
            f"  'Turing's halting problem is basically a rabbinic she'eila'\n"
            f"  'The IDF's distributed command structure solves the same problem as microservices'\n"
            f"  'Chazakah is a Bayesian prior with a 2,000-year head start'\n"
            f"  'The CAP theorem is just the trolley problem in distributed systems'\n"
            f"  'Why exponential backoff is the same logic as Nachmanides on teshuva'\n\n"
            f"The connection field should feel like a revelation, not a comparison. Don't say 'both X and Y deal with Z' — explain WHY the same underlying mechanic appears in both domains, and what that reveals.\n\n"
            f"DIVERSITY RULES (strictly enforced):\n"
            f"- The best sparks bridge DIFFERENT worlds: Torah ↔ CS, Israel news ↔ history, OS internals ↔ economics, sports strategy ↔ philosophy\n"
            f"- REJECT any spark that connects two CS concepts, two academic papers from the same course, or two obviously related topics\n"
            f"- If a pair is weak, obvious, or surface-level — OMIT IT. 3 great sparks > 8 mediocre ones\n\n"
            f"ENGAGEMENT RULES — how to reference each item:\n"
            f"- WROTE / EDITED / BUILT / ATTENDED → {JIMMY_USER_NAME} knows this. Say 'In your notes on X...' or 'you wrote that...'\n"
            f"- COURSE MATERIAL → don't assume he internalized it. Say 'Your [OS/Networks/Algo] class covered X as...' Frame as discovery.\n"
            f"- SAVED → don't assume he read it. Say 'you saved a [article/video] called [Title] that argues Y...'\n\n"
            f"ELABORATIVE INTERROGATION — for the connection field, always answer: WHY does this connection exist? "
            f"What is the deep reason the same pattern appears in both domains? What does this reveal about how the world works?\n\n"
            f"TIME SCALE MIXING — prioritize sparks that connect things from different time scales: "
            f"something from last year (e.g. Macroeconomics, Distributed Systems) with something from this week (OS, Networks, Accounting). "
            f"This is the most valuable kind of spark — it activates old knowledge through new context.\n\n"
            f"ACTIONABLE INSIGHT — end the connection with: 'This means when you see X in [domain A], look for Y in [domain B]'\n\n"
            f"STRICT FIELD RULES:\n"
            f"- title: 5-10 words. A vivid, curious observation or question — NOT 'The Connection Between X and Y'\n"
            f"- recent_item: 1 sentence naming the ACTUAL TITLE of the recent content with engagement-appropriate framing\n"
            f"- past_item: 1 sentence naming the ACTUAL TITLE of the past content with engagement-appropriate framing\n"
            f"- connection: 2-3 sentences of genuine insight. WHY does the same underlying mechanic appear in both? What does it reveal? End with the actionable insight.\n"
            f"- why_it_matters: 1 concrete, personal sentence answering: 'Why does this connection matter for understanding BOTH domains?' For SAVED/COURSE items: end with 'Want to dig into this?'\n"
            f"- icon: a single emoji that captures the spark's vibe\n\n"
            f"NEVER mention 'your knowledge base', 'your notes', 'your second brain'. Be specific about actual source titles.\n\n"
            f"Return ONLY valid JSON (no markdown):\n"
            f'[{{"title":"...","recent_item":"...","past_item":"..."'
            f',"connection":"...","why_it_matters":"...","icon":"single emoji"}}]\n\n'
            f"PAIRS:\n{pairs_ctx}",
            max_tokens=3500,
            tier="deep",
        )
        match = re.search(r'\[[\s\S]*\]', raw)
        sparks = []
        if match:
            try:
                sparks = [s for s in json.loads(match.group(0)) if isinstance(s, dict)]
            except Exception:
                pass
        return {
            "sparks": sparks,
            "days_recent": days_recent,
            "total_recent": len(recent_sample),
            "total_old": len(old_domain_by_id),
        }

    def timeline(self, weeks: int = 16, days: int = 0) -> dict:
        """Return learning activity grouped by week + flat events list for visualization.

        Args:
            weeks: Number of weeks to look back (default 16). Ignored if days > 0.
            days:  Explicit lookback in days (overrides weeks when > 0).
        """
        from datetime import datetime, timedelta, date as _date
        from collections import defaultdict

        result = self.store.collection.get(include=["metadatas", "documents"])
        if not result["metadatas"]:
            return {"weeks": [], "heatmap": [], "total": 0, "events": [], "period_weeks": weeks}

        now = datetime.now()
        if days > 0:
            cutoff = now - timedelta(days=days)
            period_weeks = max(1, days // 7)
        else:
            cutoff = now - timedelta(weeks=weeks)
            period_weeks = weeks

        # Map source → event type for structured display
        SOURCE_TYPE_MAP = {
            "canvas": "class",
            "notion": "note",
            "apple_notes": "note",
            "gmail": "note",
            "github": "note",
            "youtube": "video",
            "goodnotes": "note",
            "book": "book",
            "library": "book",
            "pocket": "note",
            "web": "note",
        }

        week_data: dict[str, dict] = {}
        day_counts: dict[str, set] = defaultdict(set)  # date → set of unique titles
        events_by_title: dict[str, dict] = {}  # title → best event dict

        today_str = _date.today().isoformat()
        TIMELINE_EXCLUDE = {"calendar"}  # Calendar skews timeline with future events

        for meta, doc in zip(result["metadatas"], result["documents"]):
            if meta.get("source") in TIMELINE_EXCLUDE:
                continue
            date_str = _extract_date(meta)
            if not date_str:
                continue
            # Skip future-dated items — timeline shows what you've learned, not what's coming
            if date_str > today_str:
                continue
            try:
                dt = datetime.fromisoformat(date_str[:10] + "T00:00:00")
            except Exception:
                continue

            src = meta.get("source", "unknown")
            title = meta.get("title", "")
            url = meta.get("url", "")
            event_type = SOURCE_TYPE_MAP.get(src.lower(), "note")

            if title:
                day_counts[date_str[:10]].add(title)

            # Build flat events list (within lookback window)
            if dt >= cutoff and title and title not in events_by_title:
                snippet = (doc or "").strip()
                # Trim to a readable snippet length
                if len(snippet) > 200:
                    snippet = snippet[:197] + "…"
                events_by_title[title] = {
                    "date": date_str[:10],
                    "title": title,
                    "snippet": snippet,
                    "source": src,
                    "type": event_type,
                    "url": url,
                }

            if dt < cutoff:
                continue

            # Week start = Monday
            week_start = (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")

            if week_start not in week_data:
                week_data[week_start] = {"sources": defaultdict(set), "titles": set(), "items": []}

            if title:
                week_data[week_start]["sources"][src].add(title)
            if title and title not in week_data[week_start]["titles"]:
                week_data[week_start]["titles"].add(title)
                week_data[week_start]["items"].append({
                    "title": title,
                    "source": src,
                    "type": event_type,
                    "date": date_str[:10],
                    "url": url,
                })

        weeks_list = []
        for week_start in sorted(week_data.keys(), reverse=True):
            data = week_data[week_start]
            dt = datetime.fromisoformat(week_start)
            items = sorted(data["items"], key=lambda x: x.get("date", ""), reverse=True)
            weeks_list.append({
                "week_start": week_start,
                "label": dt.strftime("Week of %b %-d"),
                "total_items": len(data["titles"]),
                "sources": {k: len(v) for k, v in data["sources"].items()},
                "top_items": items[:15],
            })

        # Build 365-day heatmap
        heatmap = [
            {"date": (_date.today() - timedelta(days=364 - i)).isoformat(),
             "count": len(day_counts.get((_date.today() - timedelta(days=364 - i)).isoformat(), set()))}
            for i in range(365)
        ]

        # Flat events sorted newest-first
        events = sorted(events_by_title.values(), key=lambda e: e["date"], reverse=True)

        # Streak: count consecutive days with activity ending today
        streak = 0
        check_date = _date.today()
        while True:
            if len(day_counts.get(check_date.isoformat(), set())) > 0:
                streak += 1
                check_date -= timedelta(days=1)
            else:
                break

        return {
            "weeks": weeks_list,
            "heatmap": heatmap,
            "total": sum(w["total_items"] for w in weeks_list),
            "period_weeks": period_weeks,
            "events": events,
            "streak": streak,
        }

    def upcoming(self, days: int = 14) -> dict:
        """What's on your calendar in the next N days? Date-filtered, not semantic search."""
        from datetime import date as _date, timedelta
        today = _date.today().isoformat()
        cutoff = (_date.today() + timedelta(days=days)).isoformat()

        try:
            all_data = self.store.collection.get(
                where={"source": "calendar"}, include=["documents", "metadatas"]
            )
        except Exception:
            all_data = {"ids": [], "documents": [], "metadatas": []}
        if not all_data["ids"]:
            return {"result": f"Nothing on your calendar in the next {days} days.", "events": [], "days": days}

        seen_titles: list[tuple[str, str]] = []
        events: list[tuple[int, dict]] = []
        for doc, meta in zip(all_data["documents"], all_data["metadatas"]):
            date_str = _extract_date(meta)
            if not date_str or date_str < today or date_str > cutoff:
                continue
            title = meta.get("title", "Event")
            calendar = meta.get("calendar", "")
            if _should_hide_calendar_event(title, calendar):
                continue
            normalized = _normalize_title(title)
            if any(existing_date == date_str and _word_overlap_ratio(normalized, existing_title) >= 0.8 for existing_date, existing_title in seen_titles):
                continue
            seen_titles.append((date_str, normalized))
            priority = _calendar_priority(title, calendar)
            events.append((priority, {
                "title": title,
                "date": date_str,
                "calendar": calendar,
                "account": meta.get("account", ""),
                "url": meta.get("url", ""),
                "excerpt": doc[:300],
            }))

        events.sort(key=lambda x: (x[1]["date"], x[0], _normalize_title(x[1]["title"])))

        if not events:
            return {"result": f"Nothing on your calendar in the next {days} days.", "events": [], "days": days}

        high = [event for priority, event in events if priority == 0]
        academic = [event for priority, event in events if priority == 1]
        personal = [event for priority, event in events if priority == 2]
        events = high[:12] + academic[:10] + personal[:8]
        events.sort(key=lambda x: (x["date"], _calendar_priority(x["title"], x.get("calendar", "")), _normalize_title(x["title"])))

        lines = []
        current_date = None
        for e in events:
            if e["date"] != current_date:
                current_date = e["date"]
                from datetime import date as _d
                try:
                    _dt = _d.fromisoformat(e["date"])
                    label = _dt.strftime("%A, %B ") + str(_dt.day)
                except Exception:
                    label = e["date"]
                lines.append(f"\n**{label}**")
            cal = f" _({e['calendar']})_" if e["calendar"] else ""
            lines.append(f"- {e['title']}{cal}")

        result = f"**Upcoming — next {days} days ({len(events)} events)**\n" + "\n".join(lines)
        return {"result": result, "events": events, "days": days}

    def recent(self, days: int = 14) -> dict:
        """What have you been taken in lately? Scans recent items directly by source."""
        from datetime import date as _date, timedelta
        cutoff = (_date.today() - timedelta(days=days)).isoformat()
        store = self.store

        # Recent should mean recently added to the KB, not just old source docs with future dates.
        sources_to_scan = [
            "canvas", "granola", "apple_notes", "note",
            "file", "web", "gdrive", "youtube",
            "readwise", "notion", "podcast", "bookmarks",
        ]
        source_caps = {
            "notion": 15,
            "gdrive": 10,
            "canvas": 10,
            "file": 10,
            "web": 10,
        }
        by_source: dict[str, list[dict]] = {}
        seen_titles: set[str] = set()

        def _scan_source(src: str) -> list[dict]:
            items = []
            try:
                result = store.collection.get(
                    where={"source": src},
                    limit=500,
                    include=["documents", "metadatas"],
                )
            except Exception:
                return items
            for doc, meta in zip(result["documents"], result["metadatas"]):
                date_str = _extract_recent_activity_date(meta)
                if not date_str or date_str < cutoff:
                    continue
                title = meta.get("title", "Untitled")
                if _should_exclude_recent_item(src, title):
                    continue
                items.append({
                    "title": title,
                    "date": date_str,
                    "source": src,
                    "excerpt": doc[:200],
                    "url": meta.get("url", ""),
                })
            return items

        # Parallel per-source queries (was sequential — N round trips)
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(_scan_source, src): src for src in sources_to_scan}
            for future in as_completed(futures):
                src = futures[future]
                for item in future.result():
                    key = f"{item['title']}::{src}"
                    if key not in seen_titles:
                        seen_titles.add(key)
                        by_source.setdefault(src, []).append(item)

        # Sort each source's items by date descending
        for src in by_source:
            by_source[src].sort(key=lambda x: x["date"], reverse=True)
            cap = source_caps.get(src, 8)
            by_source[src] = by_source[src][:cap]

        if not by_source:
            return {"result": f"Nothing found in the last {days} days.", "by_source": {}, "days": days}

        total = sum(len(v) for v in by_source.values())
        return {"result": f"{total} items from the last {days} days.", "by_source": by_source, "days": days}

    def daily(self) -> dict:
        """Generate a personalized daily fun fact and vocab word from the knowledge base.

        When exams are coming up within 7 days, biases toward course material for that subject
        so the daily fact/vocab is relevant to current study priorities.
        """
        import random, json as _json
        from datetime import date as _date

        if self.store.count() == 0:
            return {"fact": None, "vocab": None}

        # --- Detect upcoming exams to bias content selection ---
        upcoming_exam_topics: list[str] = []
        exam_context_note = ""
        try:
            upcoming_block = self._upcoming_summary(days=7)
            if upcoming_block:
                EXAM_KWS = ["exam", "midterm", "final", "quiz", "test"]
                lines_with_exams = [
                    l for l in upcoming_block.split("\n")
                    if any(kw in l.lower() for kw in EXAM_KWS)
                ]
                if lines_with_exams:
                    exam_context_note = (
                        f"\n\nIMPORTANT: {JIMMY_USER_NAME} has upcoming exams:\n"
                        + "\n".join(lines_with_exams[:4])
                        + "\n\nStrongly prefer the fact and vocab word to come from these exam subjects. "
                        "Make them exam-prep relevant."
                    )
                    # Extract topic keywords from exam lines
                    for line in lines_with_exams:
                        line_lower = line.lower()
                        if "os" in line_lower or "operating" in line_lower:
                            upcoming_exam_topics.append("operating systems process thread memory scheduling")
                        if "network" in line_lower:
                            upcoming_exam_topics.append("computer networks TCP IP routing protocols")
                        if "algorithm" in line_lower or "algo" in line_lower:
                            upcoming_exam_topics.append("algorithms complexity sorting graph")
                        if "account" in line_lower:
                            upcoming_exam_topics.append("financial accounting balance sheet income")
        except Exception:
            pass

        # Sample a diverse mix, biased toward exam topics if available
        # Current semester: OS, Networks, Algorithms, Accounting
        CURRENT_COURSE_SEEDS = [
            "operating systems process thread memory scheduling virtual",
            "computer networks TCP IP routing protocols packet",
            "algorithms complexity sorting graph dynamic programming",
            "financial accounting balance sheet income statement GAAP",
        ]
        VARIETY_SEEDS = [
            "book reading highlight insight quote",
            "personal note reflection idea observation",
        ]
        seed_queries = (
            upcoming_exam_topics[:3] +  # bias toward exam topics
            CURRENT_COURSE_SEEDS +
            [
                "interesting surprising counterintuitive fact",
                "technical concept definition term explained",
                "domain specific vocabulary jargon term",
                "mechanism process how it works underlying",
            ] +
            VARIETY_SEEDS
        )
        seen_ids: set[str] = set()
        docs, metas = [], []

        # Date cutoffs for filtering
        from datetime import date as _date2, timedelta as _td
        _ninety_days_ago = (_date2.today() - _td(days=90)).isoformat()
        _six_months_ago  = (_date2.today() - _td(days=180)).isoformat()

        # Preferred sources for current-semester content
        CURRENT_SOURCES   = {"canvas", "apple_notes", "note"}
        VARIETY_SOURCES   = {"goodreads", "kindle", "readwise", "pocket", "spotify", "podcast"}
        OLD_DIST_SYS_KWORDS = {"distributed", "consensus", "replication", "raft", "paxos",
                               "fault tolerance", "go channel", "goroutine"}

        def _query_one(q: str):
            try:
                res = self.store.search(q, n_results=8)
                # search() returns {"ids": [[...]], "documents": [[...]], "metadatas": [[...]]}
                return list(zip(res["documents"][0], res["metadatas"][0], res["ids"][0]))
            except Exception:
                return []

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(_query_one, q) for q in seed_queries]
            for future in as_completed(futures):
                for d, m, i in future.result():
                    if i not in seen_ids:
                        # Filter out stale canvas chunks (older than 6 months = old semester)
                        src = m.get("source", "")
                        date_str = _extract_date(m)
                        if src == "canvas" and date_str and date_str < _six_months_ago:
                            continue
                        # Filter out old distributed systems content (last semester)
                        combined_text = (d + " " + m.get("title", "") + " " + m.get("course_name", "")).lower()
                        if any(kw in combined_text for kw in OLD_DIST_SYS_KWORDS):
                            # Allow only if from a current-semester canvas chunk (recent)
                            if src == "canvas" and date_str and date_str >= _ninety_days_ago:
                                pass  # keep it — current course content
                            elif src not in CURRENT_SOURCES:
                                continue  # skip old dist-sys content from other sources
                        seen_ids.add(i)
                        docs.append(d)
                        metas.append(m)

        if not docs:
            return {"fact": None, "vocab": None}

        # Sort: recent current-course content first, then variety sources, then rest
        def _doc_priority(dm: tuple) -> int:
            d_txt, m_obj = dm
            src = m_obj.get("source", "")
            date_str = _extract_date(m_obj)
            if src in CURRENT_SOURCES:
                if date_str and date_str >= _ninety_days_ago:
                    return 0  # recent current-course — highest priority
                return 1
            if src in VARIETY_SOURCES:
                return 2  # goodreads, podcasts, etc. for variety
            return 3

        paired = list(zip(docs, metas))
        # If exam topics found, put course-source docs first but keep variety
        if upcoming_exam_topics:
            course_sources = {"canvas", "note", "apple_notes", "file"}
            course_docs  = [x for x in paired if x[1].get("source") in course_sources]
            variety_docs = [x for x in paired if x[1].get("source") in VARIETY_SOURCES]
            other_docs   = [x for x in paired if x[1].get("source") not in course_sources
                            and x[1].get("source") not in VARIETY_SOURCES]
            # Shuffle within groups for daily variety
            random.shuffle(course_docs)
            random.shuffle(variety_docs)
            random.shuffle(other_docs)
            # Mix: mostly course, sprinkle variety
            ordered = course_docs[:14] + variety_docs[:3] + other_docs[:3]
        else:
            paired.sort(key=_doc_priority)
            # Within same priority, shuffle for daily variety
            from itertools import groupby
            ordered = []
            for _, grp in groupby(paired, key=_doc_priority):
                grp_list = list(grp)
                random.shuffle(grp_list)
                ordered.extend(grp_list)

        docs  = [x[0] for x in ordered]
        metas = [x[1] for x in ordered]

        snippets = "\n\n".join([
            f"[Source: {m.get('source','?')} | {m.get('title','')[:50]}]\n{d[:400]}"
            for d, m in zip(docs[:20], metas[:20])
        ])

        from datetime import datetime as _dt
        today = _dt.now().strftime("%A, %B %d, %Y")

        prompt = f"""Today is {today}. {_user_prompt_context()}{exam_context_note}

Below are excerpts from his notes, courses, meetings, and research.

Generate TWO things for today's daily card. Both should make him think "oh right, I need to remember that."

1. FACT — A specific, surprising thing from his actual course material or notes. NOT a generic CS fact you could find on Wikipedia. Think: a counterintuitive result, a subtle mechanism, a specific algorithm property, a GAAP nuance, a networking edge case. Something that would appear on an exam and that he might have glossed over.
   - If exams are upcoming, make this exam-prep relevant.
   - 2-3 sentences max. Specific enough that it could be a standalone exam question.

2. VOCAB WORD — A term from what he's currently studying (OS, Networks, Algorithms, or Accounting). Not a word he definitely knows — a term that's used in the course but whose precise meaning he might be fuzzy on. OR a term with a subtle distinction (e.g., thrashing vs. swapping, congestion vs. flow control, revenue vs. income).
   - If exams are upcoming, pick something exam-relevant.

EXCERPTS:
{snippets}

Respond ONLY with valid JSON (no markdown fences):
{{
  "fact": {{
    "text": "The specific, surprising fact or insight — 2-3 sentences, standalone and memorable.",
    "source": "Specific source (e.g. 'OS course — virtual memory lecture', 'Networks — TCP/IP notes')"
  }},
  "vocab": {{
    "word": "The exact term",
    "definition": "One precise, complete sentence definition — technical but clear.",
    "context": "One sentence on why this matters or where it trips people up in his courses.",
    "source": "Specific source"
  }}
}}"""

        raw = self._chat(prompt, tier="fast")
        try:
            # Strip markdown fences if model added them
            clean = raw.strip()
            if clean.startswith("```"):
                clean = "\n".join(clean.split("\n")[1:])
                clean = clean.rstrip("`").strip()
            result = _json.loads(clean)
            result["date"] = _date.today().isoformat()
            return result
        except Exception:
            return {"fact": None, "vocab": None, "date": _date.today().isoformat()}


    # ── LIBRARY ──────────────────────────────────────────────────────────────────

    def library_list(self) -> dict:
        """Return all books in the KB (sources: kindle, readwise, book, goodreads).

        Groups chunks by book title, counts highlights and notes, extracts author,
        last-read date, and runs a fast theme-extraction prompt over a sample.
        """
        import json, re
        from datetime import date as _date

        BOOK_SOURCES = ["kindle", "readwise", "book", "goodreads"]

        # Filtered fetch — only book sources, not entire KB
        try:
            result = self.store.get_by_sources(BOOK_SOURCES, include=["metadatas", "documents"])
        except Exception:
            return {"books": []}

        # Group by canonical book key: (source, book_title or title)
        books: dict[str, dict] = {}  # key → book dict
        for doc_id, doc, meta in zip(result["ids"], result["documents"], result["metadatas"]):
            src = meta.get("source", "")

            # Canonical title: prefer metadata "book" field, then "title"
            raw_title = meta.get("book") or meta.get("title", "")
            # Strip common prefixes like "Kindle: " or "Readwise: "
            for prefix in ("Kindle: ", "Readwise: ", "Book: ", "Goodreads: "):
                if raw_title.startswith(prefix):
                    raw_title = raw_title[len(prefix):]

            if not raw_title:
                continue

            author = meta.get("author", "")
            date = _extract_date(meta)
            book_type = meta.get("type", "")
            asin = meta.get("asin", "")

            # Build stable book ID from source + title
            book_id = re.sub(r"[^a-z0-9_]", "_", f"{src}_{raw_title}".lower())[:80]

            if book_id not in books:
                books[book_id] = {
                    "id": book_id,
                    "title": raw_title,
                    "author": author,
                    "source": src,
                    "asin": asin,
                    "highlights_count": 0,
                    "notes_count": 0,
                    "last_read": date,
                    "chunk_ids": [],
                    "sample_text": "",
                }
            b = books[book_id]

            # Count highlights vs. notes
            if book_type in ("book_highlights", "highlight"):
                b["highlights_count"] += 1
            elif book_type in ("note", "book_note"):
                b["notes_count"] += 1
            else:
                b["highlights_count"] += 1  # default: treat as highlight

            # Track most recent date
            if date and (not b["last_read"] or date > b["last_read"]):
                b["last_read"] = date

            # Update author if we now have one
            if not b["author"] and author:
                b["author"] = author
            if not b["asin"] and asin:
                b["asin"] = asin

            b["chunk_ids"].append(doc_id)
            # Collect sample text for theme extraction (cap at ~600 chars)
            if len(b["sample_text"]) < 600:
                b["sample_text"] += " " + doc[:200]

        if not books:
            return {"books": []}

        book_list = list(books.values())

        # Run AI theme extraction on all books in one call (capped at 20 books)
        books_for_themes = sorted(book_list, key=lambda b: -(b["highlights_count"] + b["notes_count"]))[:20]
        try:
            theme_ctx = "\n".join(
                f'[{i}] "{b["title"]}" by {b["author"] or "Unknown"}: {b["sample_text"][:300].strip()}'
                for i, b in enumerate(books_for_themes)
            )
            raw = self._chat(
                "For each numbered book excerpt below, extract 2-3 theme tags (single words or short phrases) "
                "and 2-3 topic tags that connect this book to academic or real-world domains. "
                "Return ONLY a JSON array in order, one entry per book:\n"
                '[{"themes": ["tag1", "tag2"], "connected_topics": ["topic1", "topic2"]}]\n\n'
                f"BOOKS:\n{theme_ctx}",
                max_tokens=800,
                tier="fast",
            )
            m = re.search(r'\[[\s\S]*?\]', raw)
            if m:
                theme_data = json.loads(m.group(0))
                for i, b in enumerate(books_for_themes):
                    if i < len(theme_data) and isinstance(theme_data[i], dict):
                        b["themes"] = theme_data[i].get("themes", [])
                        b["connected_topics"] = theme_data[i].get("connected_topics", [])
        except Exception:
            pass

        # Clean up internal fields before returning
        for b in book_list:
            b.pop("sample_text", None)
            b.pop("chunk_ids", None)
            b.setdefault("themes", [])
            b.setdefault("connected_topics", [])

        # Sort by highlights count descending
        book_list.sort(key=lambda b: -(b["highlights_count"] + b["notes_count"]))
        return {"books": book_list}

    def library_book(self, book_id: str) -> dict:
        """Deep dive into a single book: all highlights, AI summary, cross-connections."""
        import json, re

        BOOK_SOURCES = ["kindle", "readwise", "book", "goodreads"]

        # Filtered fetch — only book sources, not entire KB
        try:
            result = self.store.get_by_sources(BOOK_SOURCES, include=["metadatas", "documents"])
        except Exception:
            return {"error": "Could not access knowledge base."}

        docs = []
        metas = []
        title = ""
        author = ""
        source = ""
        asin = ""
        last_read = ""

        for doc_id, doc, meta in zip(result["ids"], result["documents"], result["metadatas"]):
            src = meta.get("source", "")
            raw_title = meta.get("book") or meta.get("title", "")
            for prefix in ("Kindle: ", "Readwise: ", "Book: ", "Goodreads: "):
                if raw_title.startswith(prefix):
                    raw_title = raw_title[len(prefix):]
            cid = re.sub(r"[^a-z0-9_]", "_", f"{src}_{raw_title}".lower())[:80]
            if cid != book_id:
                continue
            docs.append(doc)
            metas.append(meta)
            if not title:
                title = raw_title
                author = meta.get("author", "")
                source = src
                asin = meta.get("asin", "")
            d = _extract_date(meta)
            if d and (not last_read or d > last_read):
                last_read = d

        if not docs:
            return {"error": f"Book '{book_id}' not found in library."}

        # Build highlights list (first 300 chars per chunk)
        highlights = []
        notes = []
        for doc, meta in zip(docs, metas):
            book_type = meta.get("type", "")
            date = _extract_date(meta)
            entry = {"text": doc.strip(), "date": date}
            if book_type in ("note", "book_note"):
                notes.append(entry)
            else:
                highlights.append(entry)

        # Cross-connections: search other sources for overlapping ideas
        search_q = f"{title} {author} themes ideas concepts"
        cross_docs = []
        cross_metas = []
        try:
            scored = self._hybrid_search(search_q, n_candidates=60)
            seen_book_chunks: set[str] = set()
            # Determine which chunk_ids belong to this book
            for doc, meta in zip(docs, metas):
                pass  # already have them
            for score, cdoc, cmeta, cid in scored:
                csrc = cmeta.get("source", "")
                if csrc in BOOK_SOURCES:
                    continue  # skip other books — want cross-domain hits
                craw = cmeta.get("book") or cmeta.get("title", "")
                cross_docs.append(cdoc)
                cross_metas.append(cmeta)
                if len(cross_docs) >= 8:
                    break
        except Exception:
            pass

        # AI: generate summary + key themes + cross-connections
        all_text = "\n\n".join(d[:400] for d in docs[:30])
        cross_ctx = "\n\n".join(
            f'[{cmeta.get("source","")}] {cmeta.get("title","")}: {cdoc[:200]}'
            for cdoc, cmeta in zip(cross_docs[:6], cross_metas[:6])
        )

        summary = ""
        key_themes = []
        cross_connections = []
        related_books = []

        try:
            prompt = (
                f'Book: "{title}" by {author or "Unknown"}\n\n'
                f'HIGHLIGHTS ({len(docs)} chunks):\n{all_text[:3000]}\n\n'
                f'CROSS-DOMAIN CONTEXT (from {JIMMY_USER_NAME}\'s notes, courses, work):\n{cross_ctx}\n\n'
                f'Return ONLY valid JSON (no markdown):\n'
                '{{\n'
                '  "summary": "3-4 sentence summary of the book\'s core arguments based on the highlights",\n'
                '  "key_themes": ["theme1", "theme2", "theme3"],\n'
                '  "cross_connections": [\n'
                '    {{"source_title": "...", "source_type": "...", "connection": "1 sentence"}}\n'
                '  ],\n'
                '  "related_queries": ["query to find related books"]\n'
                '}}'
            )
            raw = self._chat(prompt, max_tokens=1000, tier="fast")
            clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            parsed = json.loads(clean)
            summary = parsed.get("summary", "")
            key_themes = parsed.get("key_themes", [])
            cross_connections = parsed.get("cross_connections", [])
            # Find related books (filtered fetch instead of full scan)
            rq = parsed.get("related_queries", [])
            if rq:
                try:
                    rb_result = self.store.get_by_sources(BOOK_SOURCES, include=["metadatas"])
                    seen_rel: set[str] = {book_id}
                    for rmeta in rb_result["metadatas"]:
                        rsrc = rmeta.get("source", "")
                        rt = rmeta.get("book") or rmeta.get("title", "")
                        for prefix in ("Kindle: ", "Readwise: ", "Book: ", "Goodreads: "):
                            if rt.startswith(prefix):
                                rt = rt[len(prefix):]
                        rid = re.sub(r"[^a-z0-9_]", "_", f"{rsrc}_{rt}".lower())[:80]
                        if rid not in seen_rel and rt:
                            seen_rel.add(rid)
                            related_books.append({
                                "id": rid,
                                "title": rt,
                                "author": rmeta.get("author", ""),
                            })
                            if len(related_books) >= 5:
                                break
                except Exception:
                    pass
        except Exception:
            pass

        _, source_cards = _build_numbered_context(docs[:10], metas[:10])

        return {
            "id": book_id,
            "title": title,
            "author": author,
            "source": source,
            "asin": asin,
            "last_read": last_read,
            "highlights_count": len(highlights),
            "notes_count": len(notes),
            "highlights": highlights[:50],
            "notes": notes[:20],
            "summary": summary,
            "key_themes": key_themes,
            "cross_connections": cross_connections,
            "related_books": related_books,
            "sources": source_cards[:8],
        }

    def library_ask(self, question: str, book_id: str | None = None) -> dict:
        """Ask a question scoped to the library (or a single book)."""
        import re

        BOOK_SOURCES = ["kindle", "readwise", "book", "goodreads"]

        if book_id:
            # Filtered fetch — only book sources, not entire KB
            try:
                result = self.store.get_by_sources(BOOK_SOURCES, include=["metadatas", "documents"])
            except Exception:
                return {"answer": "Could not access knowledge base.", "sources": []}

            docs = []
            metas = []
            for doc, meta in zip(result["documents"], result["metadatas"]):
                src = meta.get("source", "")
                raw_title = meta.get("book") or meta.get("title", "")
                for prefix in ("Kindle: ", "Readwise: ", "Book: ", "Goodreads: "):
                    if raw_title.startswith(prefix):
                        raw_title = raw_title[len(prefix):]
                cid = re.sub(r"[^a-z0-9_]", "_", f"{src}_{raw_title}".lower())[:80]
                if cid == book_id:
                    docs.append(doc)
                    metas.append(meta)
        else:
            # Search across all book sources
            queries = self._expand_query(question)
            scored = self._multi_search(queries, n_candidates=50)
            book_scored = [(s, d, m, i) for s, d, m, i in scored if m.get("source") in BOOK_SOURCES]
            docs = [x[1] for x in book_scored[:20]]
            metas = [x[2] for x in book_scored[:20]]

        if not docs:
            return {
                "answer": "No book content found." + (f" Book '{book_id}' not in library." if book_id else " Ingest Kindle or Readwise highlights first."),
                "sources": [],
                "question": question,
            }

        context, sources = _build_numbered_context(docs[:20], metas[:20])
        book_titles = ", ".join({
            (m.get("book") or m.get("title", "")).replace("Kindle: ", "").replace("Readwise: ", "")
            for m in metas[:20] if m.get("book") or m.get("title")
        })

        scope_note = f" scoped to: {book_titles}" if book_titles else ""
        answer = self._chat(
            f"You are Jimmy — {JIMMY_USER_NAME}'s personal library assistant.\n"
            f"Answer the question using ONLY highlights and notes from {JIMMY_USER_NAME}'s library{scope_note}.\n"
            f"Cite sources inline with [N]. Write in second person. Be specific and direct.\n"
            f"If the answer spans multiple books, note which book each insight comes from.\n\n"
            f"LIBRARY SOURCES:\n{context}\n\n"
            f"QUESTION: {question}",
            max_tokens=2000,
        )
        return {"answer": answer, "sources": sources, "question": question}

    def library_connections(self) -> dict:
        """Find cross-book idea connections and book-to-coursework links."""
        import json, re

        BOOK_SOURCES = ["kindle", "readwise", "book", "goodreads"]

        # Filtered fetch — only book sources, not entire KB
        try:
            result = self.store.get_by_sources(BOOK_SOURCES, include=["metadatas", "documents"])
        except Exception:
            return {"connections": [], "book_to_course": []}

        # Group by book
        book_samples: dict[str, dict] = {}
        for doc, meta in zip(result["documents"], result["metadatas"]):
            src = meta.get("source", "")
            if src not in BOOK_SOURCES:
                continue
            raw_title = meta.get("book") or meta.get("title", "")
            for prefix in ("Kindle: ", "Readwise: ", "Book: ", "Goodreads: "):
                if raw_title.startswith(prefix):
                    raw_title = raw_title[len(prefix):]
            if not raw_title:
                continue
            if raw_title not in book_samples:
                book_samples[raw_title] = {
                    "author": meta.get("author", ""),
                    "text": "",
                }
            if len(book_samples[raw_title]["text"]) < 800:
                book_samples[raw_title]["text"] += " " + doc[:200]

        if len(book_samples) < 2:
            return {
                "connections": [],
                "book_to_course": [],
                "message": "Need at least 2 books in your library to find connections. Ingest Kindle or Readwise highlights.",
            }

        # Gather course/notes context for book-to-coursework links (separate filtered query)
        course_samples: list[tuple[str, str, str]] = []  # (title, source, text)
        try:
            course_result = self.store.get_by_sources(
                ["canvas", "note", "apple_notes", "notion"],
                include=["metadatas", "documents"],
            )
            for doc, meta in zip(course_result["documents"], course_result["metadatas"]):
                src = meta.get("source", "")
                title = meta.get("title", "")
                if title and len(course_samples) < 15:
                    course_samples.append((title, src, doc[:200]))
        except Exception:
            pass

        # Build prompt context
        book_ctx = "\n".join(
            f'BOOK: "{t}" by {d["author"] or "Unknown"}\nSAMPLE: {d["text"][:400].strip()}'
            for t, d in list(book_samples.items())[:12]
        )
        course_ctx = "\n".join(
            f'[{src}] {t}: {txt[:150]}'
            for t, src, txt in course_samples[:10]
        ) if course_samples else "No course/notes content available."

        raw = self._chat(
            "You are Jimmy. Find meaningful cross-book idea connections and book-to-coursework links.\n\n"
            "TASK 1 — Cross-book connections: Find 3-5 ideas that appear in MULTIPLE books. "
            "Each connection should cite specific book titles and explain the shared underlying idea.\n\n"
            "TASK 2 — Book-to-course links: Find 2-3 places where a book's ideas map directly to "
            f"{JIMMY_USER_NAME}'s coursework or personal notes. Be specific about which concept from which book "
            "connects to which course/note.\n\n"
            "Return ONLY valid JSON (no markdown):\n"
            '{{\n'
            '  "connections": [\n'
            '    {{"idea": "shared idea in 1 sentence", "books": ["Book A", "Book B"], "insight": "why this connection matters"}}\n'
            '  ],\n'
            '  "book_to_course": [\n'
            '    {{"book": "Book Title", "course_or_note": "title", "connection": "1-2 sentence explanation"}}\n'
            '  ]\n'
            '}}\n\n'
            f"BOOKS IN LIBRARY:\n{book_ctx}\n\n"
            f"RALPH'S COURSES AND NOTES:\n{course_ctx}",
            max_tokens=1500,
            tier="default",
        )
        try:
            clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            parsed = json.loads(clean)
            return {
                "connections": parsed.get("connections", []),
                "book_to_course": parsed.get("book_to_course", []),
                "total_books": len(book_samples),
            }
        except Exception:
            # Try to extract JSON from raw output
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    return {
                        "connections": parsed.get("connections", []),
                        "book_to_course": parsed.get("book_to_course", []),
                        "total_books": len(book_samples),
                    }
                except Exception:
                    pass
            return {"connections": [], "book_to_course": [], "total_books": len(book_samples)}

    def recap(self) -> dict:
        """Summarize what the user learned and did in the last 7 days."""
        from datetime import date as _date, timedelta, datetime as _dt

        if self.store.count() == 0:
            return {"result": "Nothing found in the last 7 days.", "sources": [], "period": "last 7 days"}

        cutoff = (_date.today() - timedelta(days=7)).isoformat()

        seed_queries = [
            "meeting notes discussion decision",
            "lecture notes concept learned studied",
            "work project task completed built",
            "reading highlights insight takeaway",
            "personal notes thoughts reflection",
        ]

        seen_ids: set[str] = set()
        docs: list[str] = []
        metas: list[dict] = []

        def _query_recent(q: str, include_undated: bool = False):
            try:
                res = self.store.search(q, n_results=25)
                out = []
                for doc, meta, doc_id in zip(res["documents"][0], res["metadatas"][0], res["ids"][0]):
                    if doc_id in seen_ids:
                        continue
                    date_str = _extract_date(meta)
                    if date_str:
                        if date_str >= cutoff:
                            out.append((doc, meta, doc_id))
                    elif include_undated:
                        out.append((doc, meta, doc_id))
                return out
            except Exception:
                return []

        window_days = 7
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(_query_recent, q, False) for q in seed_queries]
            for future in as_completed(futures):
                for doc, meta, doc_id in future.result():
                    if doc_id not in seen_ids:
                        seen_ids.add(doc_id)
                        docs.append(doc)
                        metas.append(meta)

        # If very few docs found, widen window to 30 days
        if len(docs) < 10:
            window_days = 30
            cutoff = (_date.today() - timedelta(days=30)).isoformat()
            seen_ids_extended = set(seen_ids)
            extra_docs, extra_metas = [], []
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = [pool.submit(_query_recent, q, True) for q in seed_queries]
                for future in as_completed(futures):
                    for doc, meta, doc_id in future.result():
                        if doc_id not in seen_ids_extended:
                            seen_ids_extended.add(doc_id)
                            extra_docs.append(doc)
                            extra_metas.append(meta)
            docs.extend(extra_docs[:40 - len(docs)])
            metas.extend(extra_metas[:40 - len(metas)])

        # If still very few docs, widen window to 90 days
        if len(docs) < 10:
            window_days = 90
            cutoff = (_date.today() - timedelta(days=90)).isoformat()
            seen_ids_extended = set(seen_ids)
            extra_docs, extra_metas = [], []
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = [pool.submit(_query_recent, q, True) for q in seed_queries]
                for future in as_completed(futures):
                    for doc, meta, doc_id in future.result():
                        if doc_id not in seen_ids_extended:
                            seen_ids_extended.add(doc_id)
                            extra_docs.append(doc)
                            extra_metas.append(meta)
            docs.extend(extra_docs[:40 - len(docs)])
            metas.extend(extra_metas[:40 - len(metas)])

        actual_period = f"last {window_days} days"

        if not docs:
            return {"result": "Nothing new found in the last 90 days.", "sources": [], "period": "last 90 days"}

        context, sources = _build_numbered_context(docs[:30], metas[:30])

        today = _dt.now().strftime("%A, %B %d, %Y")

        prompt = (
            f"Today is {today}. {_user_prompt_context()} Below is content from their personal knowledge base covering the {actual_period} — "
            f"his notes, meetings, courses, and reading.\n\n"
            f"Produce a structured weekly learning recap. Output ONLY valid JSON (no markdown fences, no prose outside JSON). "
            f"Use exactly this shape:\n"
            f'{{\n'
            f'  "narrative": "2-3 sentence paragraph. Second person, sharp, like a smart friend summarizing the week. No filler. Name actual concepts.",\n'
            f'  "topics_this_week": ["topic1", "topic2", ...],\n'
            f'  "most_active_areas": ["area1", "area2", ...],\n'
            f'  "books": [{{"title": "...", "status": "reading|finished|started"}}],\n'
            f'  "key_insights": ["One concrete takeaway from the week", ...],\n'
            f'  "connections": ["One non-obvious link between two things touched this week — a deeper structural insight, not obvious overlap"],\n'
            f'  "open_question": "Exactly one specific question worth exploring next week. Format: How does X work in Y context?"\n'
            f'}}\n\n'
            f"RULES:\n"
            f"- Draw ONLY from content below. Do not invent.\n"
            f"- topics_this_week: 3-7 specific topics (e.g. 'Virtual memory', 'TCP congestion control').\n"
            f"- most_active_areas: 2-4 high-level domains (e.g. 'Operating Systems', 'Accounting').\n"
            f"- books: only include if actual book content appears in sources. Empty array [] if none.\n"
            f"- key_insights: 2-4 concrete takeaways. Specific, not generic.\n"
            f"- connections: 1-2 items. Cross-domain preferred.\n"
            f"- open_question: exactly one question, specific.\n\n"
            f"CONTENT:\n{context}"
        )

        import json as _json
        result_text = self._chat(prompt, max_tokens=900, tier="default")

        # Try to parse the structured JSON; fall back to plain text wrapped in result key
        try:
            structured = _json.loads(result_text.strip())
        except Exception:
            # Try to extract JSON block if model wrapped it in prose
            import re as _re
            m = _re.search(r'\{[\s\S]+\}', result_text)
            if m:
                try:
                    structured = _json.loads(m.group(0))
                except Exception:
                    structured = {}
            else:
                structured = {}

        if not structured:
            structured = {"narrative": result_text}

        structured["sources"] = sources
        structured["period"] = actual_period
        return structured
