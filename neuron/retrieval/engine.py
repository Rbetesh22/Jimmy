import os
import random
import httpx
from ..storage.store import NeuronStore
from ..config import CHROMA_DIR

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
}

# Higher weight = prefer this source over others at equal semantic similarity.
# Personal written notes rank highest; passive consumption lowest.
# Canvas is authoritative for course content but sits below personal notes.
SOURCE_WEIGHTS: dict[str, float] = {
    "apple_notes":   1.45,   # personal notes — highest signal
    "note":          1.45,
    "voice_memo":    1.40,   # personal spoken notes — high signal
    "granola":       1.40,   # personal meeting notes
    "notion":        1.35,   # personal workspace notes
    "calendar":      1.35,   # Google Calendar — actual commitments and events
    "gmail":         1.30,   # sent mail and starred threads
    "gdrive":        1.30,   # docs you've written
    "kindle":        1.25,   # deliberate reading (highlighted)
    "readwise":      1.25,
    "canvas":        1.20,   # course material (required + optional mixed)
    "file":          1.15,   # manually ingested file
    "github":        1.10,
    "photos":        1.10,   # personal memory
    "videos":        1.05,
    "folder":        1.08,
    "pocket":        1.05,   # saved-for-later
    "web":           0.90,
    "youtube":       0.85,
    "youtube_liked": 0.85,
    "spotify":       0.80,
    "podcast":       0.80,
    "twitter":       0.75,
    "instagram":     0.65,
    "tiktok":        0.60,
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
    """Return a short tag describing how well the user likely knows this content."""
    from datetime import date as _date, timedelta
    today = _date.today().isoformat()
    source = meta.get("source", "")

    # Future Canvas assignments/modules = not yet covered in class
    for future_key in ("due_at", "unlock_at", "unlock_date", "available_from"):
        val = meta.get(future_key, "")
        if val and isinstance(val, str) and len(val) >= 10 and val[:10] > today:
            return "NOT YET COVERED IN COURSE"

    if source in ("github",):
        return "BUILT THIS (hands-on mastery)"

    if source in ("note", "apple_notes", "voice_memo"):
        return "PERSONAL NOTE (own thinking)"

    if source in ("granola",):
        return "MEETING YOU ATTENDED"

    date_str = _extract_date(meta)
    if not date_str:
        return ""

    six_months_ago = (_date.today() - timedelta(days=180)).isoformat()
    two_years_ago  = (_date.today() - timedelta(days=730)).isoformat()

    if date_str >= six_months_ago:
        if source == "canvas":
            return "ACTIVELY STUDYING"
        return "RECENTLY ENGAGED"

    if date_str >= two_years_ago:
        return "STUDIED PREVIOUSLY (may have faded)"

    return "OLDER MATERIAL (likely faded)"


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


class NeuronEngine:
    def __init__(self):
        self.store = NeuronStore(CHROMA_DIR)
        self._upcoming_cache: dict = {}  # cache_key → (result, timestamp)
        self._anthropic_client = None
        self._openai_client = None

    def _hybrid_search(
        self, query: str, n_candidates: int = 200
    ) -> list[tuple[float, str, dict, str]]:
        """Combine vector + BM25 via Reciprocal Rank Fusion, then apply source/recency weights.

        Returns (composite_score, doc, meta, id) sorted best-first.
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
            scored.append((rrf_score * sw * rw, doc, meta, doc_id))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def _chat(self, prompt: str, max_tokens: int = 2048, model: str = "claude-sonnet-4-6") -> str:
        # Read keys fresh each call so .env changes take effect without restart
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

        if anthropic_key:
            import anthropic
            # Re-create client if key changed
            if self._anthropic_client is None or getattr(self._anthropic_client, '_api_key', None) != anthropic_key:
                self._anthropic_client = anthropic.Anthropic(api_key=anthropic_key)
                self._anthropic_client._api_key = anthropic_key
            msg = self._anthropic_client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text

        if openai_key:
            from openai import OpenAI
            try:
                if self._openai_client is None:
                    self._openai_client = OpenAI(api_key=openai_key)
                # Map Anthropic model names to OpenAI equivalents
                oai_model = "gpt-4o" if "opus" in model else "gpt-4o-mini"
                response = self._openai_client.chat.completions.create(
                    model=oai_model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                return response.choices[0].message.content
            except Exception as e:
                if "quota" in str(e).lower() or "429" in str(e):
                    pass  # fall through to Ollama
                else:
                    raise

        # Fallback: local Ollama
        from openai import OpenAI
        client = OpenAI(
            base_url="http://localhost:11434/v1",
            api_key="ollama",
            http_client=httpx.Client(trust_env=False),
        )
        response = client.chat.completions.create(
            model="llama3.1:8b",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content

    def _expand_query(self, question: str) -> list[str]:
        """Generate 3 alternative search angles for a question via a fast LLM call.

        Returns [original_question, alt1, alt2, alt3].  Falls back to [question] on failure.
        Uses claude-haiku for speed — this runs before every search.
        """
        import json, re
        # Skip LLM for short/simple queries — not worth the latency
        if len(question.split()) <= 3 or len(question) < 20:
            return [question]
        raw = self._chat(
            f"Generate 3 concise search queries to find ALL relevant information for this question "
            f"in a personal knowledge base (notes, emails, calendar, Canvas LMS, meetings, etc.).\n"
            f"Cover different angles: terminology variations, related entities, source-specific language.\n"
            f"Question: {question}\n"
            f"Output ONLY a JSON array of 3 strings, nothing else.",
            max_tokens=200,
            model="claude-haiku-4-5-20251001",
        )
        m = re.search(r'\[[\s\S]*?\]', raw)
        if m:
            try:
                alts = [q for q in json.loads(m.group(0)) if isinstance(q, str)][:3]
                return [question] + alts
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
        seen: set[str] = set()
        events: list[tuple[str, str, int]] = []  # (date, title, priority)
        IMPORTANT_KEYWORDS = {"exam", "midterm", "final", "quiz", "test", "due", "deadline", "interview", "meeting", "presentation"}
        for meta in (all_data.get("metadatas") or []):
            date_str = _extract_date(meta)
            if not date_str or date_str < today or date_str > cutoff:
                continue
            title = meta.get("title", "")
            key = f"{date_str}::{title}"
            if key in seen:
                continue
            seen.add(key)
            # Priority: 0 = high (exams/deadlines), 1 = normal
            priority = 0 if any(kw in title.lower() for kw in IMPORTANT_KEYWORDS) else 1
            events.append((date_str, title, priority))
        # Sort by date, then by priority (important first within same date)
        events.sort(key=lambda x: (x[0], x[2]))
        # Keep up to 80: all high-priority + fill with normal up to cap
        high = [(d, t) for d, t, p in events if p == 0]
        normal = [(d, t) for d, t, p in events if p == 1]
        events_final = high[:60] + normal[:20]
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

    def ask(self, question: str, n_results: int = 25) -> dict:
        from datetime import datetime
        _now = datetime.now()
        today = _now.strftime("%A, %B ") + str(_now.day) + _now.strftime(", %Y")

        queries = self._expand_query(question)
        scored = self._multi_search(queries, n_candidates=200)

        # Dedup across all sources by title to avoid the same chunk appearing twice
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

        answer = self._chat(
            f"You are Neuron — a second brain built from this person's actual notes, meetings, courses, and work.\n"
            f"Today is {today}.{upcoming_section}\n\n"
            f"KNOWLEDGE CALIBRATION — each source is tagged with the person's likely familiarity:\n"
            f"- ⚠ NOT YET COVERED IN COURSE: in their curriculum but not taught yet. Flag it — don't assume they know it.\n"
            f"- ⚠ ACTIVELY STUDYING: current coursework, may still have gaps.\n"
            f"- ⚠ BUILT THIS (hands-on mastery): they coded or built this — deep familiarity, be technical.\n"
            f"- ⚠ PERSONAL NOTE: their own thinking — treat as their stated understanding.\n"
            f"- ⚠ STUDIED PREVIOUSLY (may have faded): remind them of key details.\n"
            f"- ⚠ OLDER MATERIAL (likely faded): jog their memory, don't assume fluency.\n\n"
            f"STRICT RULES:\n"
            f"- Answer ONLY from what is explicitly stated in the sources. Do not infer or fill gaps.\n"
            f"- If the sources don't contain enough to answer, say exactly that.\n"
            f"- Quote or closely paraphrase actual content — cite every claim inline like [1][2].\n"
            f"- Name specific people, projects, dates, and decisions from the sources.\n"
            f"- Sources marked UNREAD/SAVED: say 'you've saved' not 'you read/watched'.\n"
            f"- Do not pad the answer — stop when the sources run out of relevant information.\n"
            f"- NEVER infer habits, routines, or frequency from individual data points.\n\n"
            f"SOURCES:\n{context}\n\n"
            f"QUESTION: {question}",
            max_tokens=4000,
        )
        return {"answer": answer, "sources": sources, "question": question}

    def context_pack(self, topic: str, n_results: int = 30) -> dict:
        queries = self._expand_query(topic)
        scored = self._multi_search(queries, n_candidates=200)[:n_results]
        docs  = [x[1] for x in scored]
        metas = [x[2] for x in scored]

        if not docs:
            return {"context_pack": f"Nothing found about '{topic}'.", "sources": [], "topic": topic}

        context, sources = _build_numbered_context(docs, metas)
        pack = self._chat(
            f"You are Neuron. Build a comprehensive personal briefing on \"{topic}\" from this person's actual knowledge.\n\n"
            f"Go deep — pull every relevant detail from the sources. Quote directly where it adds value.\n\n"
            f"## What I Know\nEverything relevant they've written, learned, or noted [N]. Be exhaustive and specific.\n\n"
            f"## Key People & Context\nEvery relevant person, project, decision, or deadline — with detail.\n\n"
            f"## How This Connects\nConnections to other areas of their knowledge. What does this tie into?\n\n"
            f"## What's Unresolved\nOpen questions, tensions, or things they kept returning to without resolution.\n\n"
            f"SOURCES:\n{context}",
            max_tokens=4000,
            model="claude-opus-4-6",
        )
        return {"context_pack": pack, "sources": sources, "topic": topic}

    def resurface(self, topic: str, n_results: int = 20) -> dict:
        queries = self._expand_query(topic)
        scored = self._multi_search(queries, n_candidates=200)[:n_results]
        docs  = [x[1] for x in scored]
        metas = [x[2] for x in scored]

        if not docs:
            return {"result": f"Nothing found related to '{topic}'.", "sources": [], "topic": topic}

        context, sources = _build_numbered_context(docs, metas)
        result = self._chat(
            f"You are Neuron. The person is thinking about \"{topic}\". "
            f"Surface past insights from their notes they may have forgotten.\n\n"
            f"Be specific — quote things directly, reference actual projects/conversations/dates [N]. "
            f"Highlight the most surprising or actionable things. "
            f"Show connections across sources — what patterns emerge?\n\n"
            f"SOURCES:\n{context}",
            max_tokens=2000,
        )
        return {"result": result, "sources": sources, "topic": topic}

    def connections(self, topic: str, n_results: int = 20) -> dict:
        queries = self._expand_query(topic)
        scored = self._multi_search(queries, n_candidates=200)[:n_results]
        docs  = [x[1] for x in scored]
        metas = [x[2] for x in scored]

        if not docs:
            return {"result": f"Nothing found related to '{topic}'.", "sources": [], "topic": topic}

        context, sources = _build_grouped_context(docs, metas)
        result = self._chat(
            f"You are Neuron. Show how \"{topic}\" threads through this person's knowledge base.\n\n"
            f"## Where It Shows Up\nSpecific places this appears — courses, meetings, notes, work [N].\n\n"
            f"## Common Threads\nIdeas that repeat across multiple sources.\n\n"
            f"## Tensions\nWhere sources disagree, contradict, or show changing views.\n\n"
            f"## The Bigger Picture\nWhat does the pattern across all sources reveal?\n\n"
            f"SOURCES (grouped by type):\n{context}"
        )
        return {"result": result, "sources": sources, "topic": topic}

    def digest(self, sample_size: int = 60) -> dict:
        """Daily briefing — uses targeted searches instead of full collection scan."""
        from datetime import datetime
        _now = datetime.now()
        today = _now.strftime("%A, %B ") + str(_now.day) + _now.strftime(", %Y")

        if self.store.count() == 0:
            return {"result": "Knowledge base is empty.", "sources": [], "topic": "digest"}

        seed_queries = [
            # Ideas and concepts being studied
            "idea concept theory framework insight argument",
            "book reading highlight chapter lesson learned",
            "article essay thesis claim evidence",
            "research paper finding result conclusion",
            "podcast lecture talk explanation",
            # Academic — broad, not person-specific
            "class lecture course notes concepts",
            "definition explained example counterexample",
            "problem question hypothesis wondering",
            # Synthesis and connections
            "connects relates similar parallel pattern",
            "contrast difference tension paradox",
            "implication consequence therefore means",
            # Topic coverage — broad intellectual areas
            "history philosophy ethics politics economics",
            "technology science mathematics physics",
            "literature writing language culture art",
            "religion theology ethics tradition",
            "business strategy product market",
            "artificial intelligence machine learning",
        ]
        best: dict[str, tuple] = {}
        for query in seed_queries:
            for score, doc, meta, doc_id in self._hybrid_search(query, n_candidates=80):
                if doc_id not in best or score > best[doc_id][0]:
                    best[doc_id] = (score, doc, meta, doc_id)

        sorted_items = sorted(best.values(), key=lambda x: x[0], reverse=True)[:sample_size]
        all_docs  = [x[1] for x in sorted_items]
        all_metas = [x[2] for x in sorted_items]

        context, sources = _build_numbered_context(all_docs, all_metas)

        result = self._chat(
            f"You are Neuron, a learning assistant and second brain. Today is {today}.\n\n"
            f"Below are excerpts from the user's knowledge base — things they've read, highlighted, saved, "
            f"and studied. Your job is to surface what's intellectually alive in their library right now.\n\n"
            f"Write a daily learning briefing grounded entirely in the sources below. "
            f"Every claim must cite a source [N]. Do not invent anything not in the sources.\n\n"
            f"## What You're Studying\n"
            f"2–3 sentences identifying the main topics or ideas the user is actively engaging with, "
            f"based on what's in their library. Name actual books, articles, courses, or concepts [N].\n\n"
            f"## Ideas Worth Sitting With\n"
            f"2–3 specific ideas, arguments, or questions from the sources that deserve attention today. "
            f"Quote or paraphrase directly [N]. Focus on what's intellectually interesting, not administrative.\n\n"
            f"## Connections\n"
            f"Identify 1–2 non-obvious links between different things in the knowledge base. "
            f"Format: '[Concept/source A] and [concept/source B] both grapple with X, because...' [N].\n\n"
            f"## One Thread to Pull\n"
            f"Name exactly ONE concept, thinker, or open question from the sources that is worth going deeper on today. "
            f"Be specific about why now [N].\n\n"
            f"Rules:\n"
            f"- Under 400 words total\n"
            f"- No scheduling, no life admin, no generic encouragement\n"
            f"- Never paste event titles, email subjects, or calendar entries verbatim — they are context only\n"
            f"- Never use bullet lists (- or *) — write in natural prose paragraphs\n"
            f"- No source citations like [1] or [N] — just write naturally\n"
            f"- Write like a brilliant study partner who has read everything in the library\n\n"
            f"KNOWLEDGE SOURCES:\n{context}",
            max_tokens=1400,
        )
        return {"result": result, "sources": sources, "topic": "digest"}

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
        best_fact: dict = {}
        for q in fact_queries:
            for score, doc, meta, doc_id in self._hybrid_search(q, n_candidates=40):
                if doc_id not in best_fact or score > best_fact[doc_id][0]:
                    best_fact[doc_id] = (score, doc, meta, doc_id)
        fact_items = sorted(best_fact.values(), key=lambda x: x[0], reverse=True)[:20]
        fact_docs = [x[1] for x in fact_items]
        fact_context = "\n\n".join(f"[{i+1}] {d[:400]}" for i, d in enumerate(fact_docs))

        # Sample for vocabulary — look for domain-specific terminology
        vocab_queries = [
            "term definition concept theory principle",
            "named after called known as referred to",
            "technical jargon discipline field domain",
            "Greek Latin root derived from means",
            "phenomenon effect law theorem conjecture",
        ]
        best_vocab: dict = {}
        for q in vocab_queries:
            for score, doc, meta, doc_id in self._hybrid_search(q, n_candidates=40):
                if doc_id not in best_vocab or score > best_vocab[doc_id][0]:
                    best_vocab[doc_id] = (score, doc, meta, doc_id)
        vocab_items = sorted(best_vocab.values(), key=lambda x: x[0], reverse=True)[:20]
        vocab_docs = [x[1] for x in vocab_items]
        vocab_context = "\n\n".join(f"[{i+1}] {d[:400]}" for i, d in enumerate(vocab_docs))

        today = datetime.now().strftime("%A, %B %d")

        # Generate fact
        RALPH_CONTEXT = (
            "Ralph is a Columbia CS MS student starting at Datadog (query engineering team) on Aug 31 2026. "
            "He is Jewish, observant, and interested in Torah study. "
            "He is preparing for exams in Operating Systems, Computer Networks, and Algorithms this week. "
            "He is also preparing for Datadog by studying Apache Arrow, Trino, ClickHouse, and Apache Calcite."
        )

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
            f"Write a single motivational sentence (1 sentence only, no more) for Ralph.\n"
            f"{RALPH_CONTEXT}\n"
            f"Today is {today}. Make it specific to his situation — Datadog prep, Columbia finals, "
            f"or his Jewish values. Keep it genuine, not cheesy. "
            f"Return ONLY the sentence, nothing else.",
            max_tokens=80,
            model="claude-haiku-4-5-20251001",
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
        """Two-pass topic graph: nodes first, then grounded edges. Cache to ~/.neuron/graph_cache.json."""
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
        raw_nodes = self._chat(node_prompt, max_tokens=4000, model="claude-opus-4-6")
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
        raw_edges = self._chat(edge_prompt, max_tokens=3000, model="claude-opus-4-6")
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

        cache_path = Path.home() / ".neuron" / "graph_cache.json"
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
            f'You are Neuron. Today is {today}. You found {unique_titles} unique documents and {n} relevant chunks about "{label}". '
            f'Write a COMPREHENSIVE, information-dense summary about what this person knows, has done, and has saved related to this topic.\n\n'
            f'REQUIREMENTS:\n'
            f'- Use ALL {n} sources — extract specifics from each one that adds new information\n'
            f'- Name EVERY specific title, person, project, date, course, employer, statistic you find in the sources\n'
            f'- Quote actual content directly when it\'s specific and revealing\n'
            f'- Note counts and patterns: "6 notes mention X", "across 3 courses you covered Y", "your meetings with Z cover..."\n'
            f'- Write 6-10 sentences of genuine detail — not just "you have notes on X"\n'
            f'- Write in second person ("Your notes show...", "You\'ve done...", "You covered...")\n'
            f'- CRITICAL: distinguish time periods explicitly — recent activity vs. things from years ago\n'
            f'- Sources marked · UNREAD or · SAVED: not yet consumed — say "saved" not "read"\n'
            f'- Source dates shown as · YYYY-MM-DD — use them to be specific\n'
            f'- Every sentence must add NEW information — no repetition, no filler\n'
            f'- NEVER infer habits or routines from single data points\n'
            f'- If you see meeting notes, name the people and decisions. If you see assignments, name the specific tasks.\n'
            f'- If you see course material, name the specific concepts, algorithms, or theories covered.\n\n'
            f'SOURCES ({n} chunks from {unique_titles} unique documents):\n{context}'
        )
        summary = self._chat(prompt, max_tokens=1200, model="claude-sonnet-4-6")
        return {"summary": summary, "sources": source_cards}

    def practice(self, topic: str, n_results: int = 20) -> dict:
        """Generate mixed practice exercises from the user's actual notes and courses on a topic."""
        import json, re
        queries = self._expand_query(topic)
        scored = self._multi_search(queries, n_candidates=200)[:n_results]
        docs  = [x[1] for x in scored]
        metas = [x[2] for x in scored]

        if not docs:
            return {"exercises": [], "topic": topic,
                    "message": f"Nothing found about '{topic}' in your knowledge base. Try ingesting some courses or notes on this topic first."}

        context, sources = _build_numbered_context(docs, metas)
        from datetime import date as _date
        today = _date.today().isoformat()

        raw = self._chat(
            f'You are Neuron, a personal tutor. Today is {today}. The person wants to practice "{topic}".\n\n'
            f'Based ONLY on their actual knowledge base below, generate 5 practice exercises that test what they have actually studied.\n\n'
            f'EXERCISE MIX:\n'
            f'- 2 concept questions (recall/explain a specific thing from their notes)\n'
            f'- 2 application questions (apply knowledge to a new scenario)\n'
            f'- 1 coding challenge (if the topic involves programming — otherwise make it a third concept question)\n\n'
            f'REQUIREMENTS:\n'
            f'- Reference specific titles, course names, professor names, or highlights from the sources\n'
            f'- Start easy, end hard\n'
            f'- For coding questions: include starter code or a clear specification\n'
            f'- The answer field must be a complete, correct answer they can learn from\n'
            f'- explanation must be 2-4 sentences tying back to their actual notes\n\n'
            f'Return ONLY valid JSON (no markdown):\n'
            f'[{{"type":"concept|application|coding","question":"...","difficulty":"easy|medium|hard",'
            f'"answer":"...","explanation":"...","source_hint":"From your [exact title/source]..."}}]\n\n'
            f'SOURCES:\n{context}',
            max_tokens=3000,
        )
        m = re.search(r'\[[\s\S]*\]', raw)
        exercises = []
        if m:
            try:
                exercises = [e for e in json.loads(m.group(0)) if isinstance(e, dict)]
            except Exception:
                pass
        return {"exercises": exercises, "topic": topic, "sources": sources}

    def evaluate_answer(self, question: str, user_answer: str, correct_answer: str,
                        explanation: str, topic: str) -> dict:
        """Evaluate a user's practice answer and give personalized feedback."""
        result = self._chat(
            f'The person is practicing "{topic}".\n\n'
            f'QUESTION: {question}\n\n'
            f'THEIR ANSWER: {user_answer}\n\n'
            f'CORRECT ANSWER: {correct_answer}\n\n'
            f'EXPLANATION: {explanation}\n\n'
            f'Give feedback in this exact JSON format (no markdown):\n'
            f'{{"score":"correct|partial|incorrect",'
            f'"feedback":"2-3 sentences — be encouraging, specific about what was right/wrong",'
            f'"key_gap":"1 sentence on the main thing they missed (null if correct)",'
            f'"follow_up":"1 related question to deepen understanding"}}\n\n'
            f'Be encouraging but honest. If partially correct, celebrate what they got right.',
            max_tokens=500,
            model="claude-haiku-4-5-20251001",
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
        from datetime import date as _date, timedelta
        today = _date.today().isoformat()
        cutoff_recent = (_date.today() - timedelta(days=days_recent)).isoformat()
        cutoff_old    = (_date.today() - timedelta(days=days_old)).isoformat()
        SPARK_EXCLUDE = {"calendar"}
        # High-signal sources: prefer content user actively studied/read/wrote
        HIGH_SIGNAL_SOURCES = {
            "canvas", "apple_notes", "note", "granola", "kindle", "readwise",
            "notion", "pocket", "youtube", "podcast", "file", "gdrive",
        }

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
            return {"sparks": [], "message": f"No content found from the last {days_recent} days. Try syncing sources or ingesting something new."}
        if not old_domain_by_id:
            return {"sparks": [], "message": "Not enough historical content to find connections. Keep using Neuron and check back later."}

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
        try:
            recent_docs_result = self.store.collection.get(
                ids=selected_recent_ids, include=["documents", "metadatas"]
            )
        except Exception:
            return {"sparks": [], "message": "Could not fetch recent documents."}

        recent_sample = list(zip(
            recent_docs_result["documents"],
            recent_docs_result["metadatas"],
            recent_docs_result["ids"],
        ))

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
            model="claude-haiku-4-5-20251001",
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

        # Search old KB with abstract queries — finds cross-domain matches
        candidate_pairs: list[tuple] = []
        seen_old_domains: set[str] = set()

        for theme_obj in themes[:8]:
            idx = min(max(int(theme_obj.get("item_idx", 1)) - 1, 0), len(recent_sample) - 1)
            r_doc, r_meta, r_id = recent_sample[idx]
            r_domain = _domain_key(r_meta)
            r_course = r_meta.get("course_name", r_meta.get("course_code", ""))
            search_query = theme_obj.get("query") or r_doc[:300]
            try:
                results = self.store.search(search_query, n_results=40)
            except Exception:
                continue

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
                if cand_domain in seen_old_domains:
                    continue
                seen_old_domains.add(cand_domain)
                candidate_pairs.append((r_doc, r_meta, cand_doc, cand_meta, theme_obj.get("theme", "")))
                break

        if not candidate_pairs:
            return {"sparks": [], "message": "No cross-domain connections found yet. Keep building your knowledge base."}

        # Format pairs for Claude with the abstract theme as a framing hint
        def _label(meta: dict, tag: str) -> str:
            src = meta.get("source", "")
            course = meta.get("course_name", meta.get("course_code", ""))
            date = _extract_date(meta) or tag.lower()
            loc = f"{src} / {course}" if course else src
            return f"[{tag} · {date} · {loc}]"

        pairs_ctx = "\n\n".join(
            f"PAIR {i+1}:\n"
            f"  ABSTRACT THEME: {theme or '(semantic match)'}\n"
            f"  RECENT {_label(r_m, 'RECENT')}\n"
            f"  Title: {r_m.get('title', '')}\n"
            f"  \"{r_d[:350]}\"\n\n"
            f"  PAST   {_label(o_m, 'PAST')}\n"
            f"  Title: {o_m.get('title', '')}\n"
            f"  \"{o_d[:350]}\""
            for i, (r_d, r_m, o_d, o_m, theme) in enumerate(candidate_pairs)
        )

        raw = self._chat(
            f"You are Neuron — a second brain that reveals surprising intellectual connections across someone's knowledge.\n"
            f"Today is {today}. Ralph is a Columbia student interested in Israel/Middle East, Torah, AI/startups, and finance.\n\n"
            f"These pairs were matched on an ABSTRACT PRINCIPLE — your job: articulate the specific non-obvious insight.\n\n"
            f"WHAT MAKES A GREAT SPARK:\n"
            f"- Specific to the actual content — quote concepts, names, titles from the text\n"
            f"- Feels like an 'aha!' — something he genuinely wouldn't have noticed\n"
            f"- Good: 'The CAP theorem trade-offs from Networks explains why blockchain consensus is impossible to scale — same mathematical constraint'\n"
            f"- Bad: 'Both deal with complexity' or 'These topics overlap in interesting ways'\n"
            f"- Actionable: how does knowing this connection help him right now?\n\n"
            f"STRICT RULES:\n"
            f"- If a pair's connection is weak or generic — OMIT IT ENTIRELY\n"
            f"- recent_item: 1 sentence on what he's studying NOW (quote specific concepts)\n"
            f"- past_item: 1 sentence on what he learned BEFORE (name the source, course, or date)\n"
            f"- connection: 2-3 sentences of genuine insight using specific terms from BOTH texts\n"
            f"- why_it_matters: one concrete payoff for exams, work, or understanding — not generic\n"
            f"- title: 5-8 words naming the actual topics connected\n\n"
            f"Return ONLY valid JSON (2-4 great sparks beats 8 weak ones):\n"
            f'[{{"title":"...","recent_item":"...","past_item":"...",'
            f'"connection":"...","why_it_matters":"...","icon":"single emoji"}}]\n\n'
            f"PAIRS:\n{pairs_ctx}",
            max_tokens=3000,
            model="claude-opus-4-6",
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

    def timeline(self, weeks: int = 16) -> dict:
        """Return learning activity grouped by week for visualization."""
        from datetime import datetime, timedelta, date as _date
        from collections import defaultdict

        result = self.store.collection.get(include=["metadatas", "documents"])
        if not result["metadatas"]:
            return {"weeks": [], "heatmap": [], "total": 0}

        now = datetime.now()
        cutoff = now - timedelta(weeks=weeks)

        week_data: dict[str, dict] = {}
        day_counts: dict[str, set] = defaultdict(set)  # date → set of unique titles

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

            title = meta.get("title", "")
            if title:
                day_counts[date_str[:10]].add(title)

            if dt < cutoff:
                continue

            # Week start = Monday
            week_start = (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")
            src = meta.get("source", "unknown")
            title = meta.get("title", "")
            url = meta.get("url", "")

            if week_start not in week_data:
                week_data[week_start] = {"sources": defaultdict(set), "titles": set(), "items": []}

            if title:
                week_data[week_start]["sources"][src].add(title)
            if title and title not in week_data[week_start]["titles"]:
                week_data[week_start]["titles"].add(title)
                week_data[week_start]["items"].append({
                    "title": title,
                    "source": src,
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

        return {
            "weeks": weeks_list,
            "heatmap": heatmap,
            "total": sum(w["total_items"] for w in weeks_list),
            "period_weeks": weeks,
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

        seen_titles: set[str] = set()
        events: list[dict] = []
        for doc, meta in zip(all_data["documents"], all_data["metadatas"]):
            date_str = _extract_date(meta)
            if not date_str or date_str < today or date_str > cutoff:
                continue
            title = meta.get("title", "Event")
            key = f"{date_str}::{title}"
            if key in seen_titles:
                continue
            seen_titles.add(key)
            events.append({
                "title": title,
                "date": date_str,
                "calendar": meta.get("calendar", ""),
                "account": meta.get("account", ""),
                "url": meta.get("url", ""),
                "excerpt": doc[:300],
            })

        events.sort(key=lambda x: x["date"])

        if not events:
            return {"result": f"Nothing on your calendar in the next {days} days.", "events": [], "days": days}

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

        # Sources most likely to have recently-dated content
        sources_to_scan = [
            "calendar", "canvas", "granola", "apple_notes", "note",
            "file", "web", "gmail", "gdrive", "spotify", "youtube",
            "readwise", "notion", "podcast", "bookmarks",
        ]

        by_source: dict[str, list[dict]] = {}
        seen_titles: set[str] = set()

        for src in sources_to_scan:
            try:
                result = store.collection.get(
                    where={"source": src},
                    limit=500,
                    include=["documents", "metadatas"],
                )
            except Exception:
                continue

            for doc, meta in zip(result["documents"], result["metadatas"]):
                date_str = _extract_date(meta)
                if not date_str or date_str < cutoff:
                    continue
                title = meta.get("title", "Untitled")
                key = f"{title}::{src}"
                if key in seen_titles:
                    continue
                seen_titles.add(key)
                by_source.setdefault(src, []).append({
                    "title": title,
                    "date": date_str,
                    "source": src,
                    "excerpt": doc[:200],
                    "url": meta.get("url", ""),
                })

        # Sort each source's items by date descending
        for src in by_source:
            by_source[src].sort(key=lambda x: x["date"], reverse=True)

        if not by_source:
            return {"result": f"Nothing found in the last {days} days.", "by_source": {}, "days": days}

        total = sum(len(v) for v in by_source.values())
        return {"result": f"{total} items from the last {days} days.", "by_source": by_source, "days": days}

