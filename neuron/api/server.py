"""
Neuron API server.
Run with: neuron serve
Local:  http://localhost:7700
Cloud:  deploy this behind nginx/Railway/Fly.io
"""
import io
import os
import re
import sys
import time
import asyncio
import tempfile
import logging
import threading
from pathlib import Path
from typing import Annotated
from collections import defaultdict

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Request, Header, Depends, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from ..ingestion.base import Document
from ..storage.store import NeuronStore
from ..retrieval.engine import NeuronEngine
from ..config import CHROMA_DIR

logger = logging.getLogger("neuron.api")

app = FastAPI(title="Neuron", version="0.1.0")

# ── IN-MEMORY TTL CACHE ─────────────────────────────────────────────────────────
# Lightweight dict cache with TTL so hot endpoints skip disk I/O entirely.
# Structure: { cache_key: {"data": ..., "expires_at": float} }
_mem_cache: dict = {}

def _mc_get(key: str):
    """Return cached value or None if missing/expired."""
    entry = _mem_cache.get(key)
    if entry and time.monotonic() < entry["expires_at"]:
        return entry["data"]
    return None

def _mc_set(key: str, value, ttl_seconds: int):
    """Store value in memory cache with TTL."""
    _mem_cache[key] = {"data": value, "expires_at": time.monotonic() + ttl_seconds}

def _mc_delete(key: str):
    """Invalidate a cache entry."""
    _mem_cache.pop(key, None)

def _mc_delete_prefix(prefix: str):
    """Invalidate all cache entries whose keys start with prefix."""
    keys = [k for k in list(_mem_cache.keys()) if k.startswith(prefix)]
    for k in keys:
        _mem_cache.pop(k, None)

# ── SERVER START TIME (for uptime tracking) ─────────────────────────────────────
_SERVER_START_TIME: float = time.monotonic()

# ── CACHE HIT/MISS STATS ─────────────────────────────────────────────────────────
_cache_stats: dict = {"daily": "miss", "spark": "miss", "analogies": "miss"}
_cache_stats_lock = threading.Lock()

def _record_cache_hit(name: str):
    with _cache_stats_lock:
        _cache_stats[name] = "hit"

def _record_cache_miss(name: str):
    with _cache_stats_lock:
        _cache_stats[name] = "miss"

# ── PER-IP RATE LIMITING ──────────────────────────────────────────────────────────
_rate_limit: dict = defaultdict(list)  # ip -> [timestamps]
_rate_limit_lock = threading.Lock()

def _check_rate_limit(ip: str, endpoint: str, max_per_minute: int = 10) -> bool:
    now = time.time()
    with _rate_limit_lock:
        times = [t for t in _rate_limit[ip] if now - t < 60]
        _rate_limit[ip] = times
        if len(times) >= max_per_minute:
            return False
        _rate_limit[ip].append(now)
    return True

# TTL constants (seconds)
_TTL_DAILY       = 12 * 3600   # 12 hours
_TTL_SPARK       = 1  * 3600   # 1 hour
_TTL_ANALOGIES   = 2  * 3600   # 2 hours
_TTL_CROSS       = 2  * 3600   # 2 hours
_TTL_SUGGESTIONS = 30 * 60     # 30 minutes
_TTL_LIBRARY     = 5  * 60     # 5 minutes
_TTL_TODAY       = 15 * 60     # 15 minutes
_TTL_RECS        = 15 * 60     # 15 minutes for /recommendations

# ── API KEY AUTH ────────────────────────────────────────────────────────────────
# Set NEURON_API_KEY in .env to enable authentication.
# When set, all sensitive endpoints require the header: X-API-Key: <your-key>
# Leave empty (default) to disable auth for local-only use.
_API_KEY = os.environ.get("NEURON_API_KEY", "").strip()


async def verify_api_key(x_api_key: str = Header(None)):
    if _API_KEY and x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


_auth = [Depends(verify_api_key)]


# ── SANITIZE CHUNKS BEFORE SENDING TO LLM ──────────────────────────────────────
_EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')
_PHONE_RE = re.compile(r'\b(?:\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b')
_MAX_CHUNK_LEN = 1000


def sanitize_chunk(text: str) -> str:
    """Minimize unnecessary PII in text before it is sent to an LLM API.

    - Truncates very long chunks to 1000 chars.
    - Replaces email addresses with [email].
    - Replaces phone numbers with [phone].
    Personal content is kept intact — only obvious contact PII is masked.
    """
    text = text[:_MAX_CHUNK_LEN]
    text = _EMAIL_RE.sub('[email]', text)
    text = _PHONE_RE.sub('[phone]', text)
    return text


# Request logging middleware — structured log with method, path, response_time_ms, status_code, client_ip
class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        client_ip = request.client.host if request.client else "-"
        logger.info(
            "method=%s path=%s response_time_ms=%.1f status_code=%d client_ip=%s",
            request.method,
            request.url.path,
            duration_ms,
            response.status_code,
            client_ip,
        )
        response.headers["X-Response-Time"] = f"{duration_ms:.1f}ms"
        return response

class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique X-Request-ID to every response for tracing / debugging."""
    async def dispatch(self, request: Request, call_next):
        import uuid
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response

app.add_middleware(RequestIDMiddleware)
app.add_middleware(RequestLogMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Allow browser extension and local web UI to call the API.
# Restricted to localhost and the Chrome extension origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:7700",
        "http://127.0.0.1:7700",
    ],
    allow_origin_regex=r"chrome-extension://[a-p]{32}",
    allow_methods=["*"],
    allow_headers=["*"],
)

UI_DIR = Path(__file__).parent.parent / "ui"


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Return structured JSON error instead of plain 500 for unhandled exceptions."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "type": type(exc).__name__},
    )


@app.get("/health")
async def health():
    from datetime import datetime
    uptime_seconds = int(time.monotonic() - _SERVER_START_TIME)
    kb_size = 0
    bm25_loaded = False
    try:
        store = get_store()
        kb_size = store.count()
        bm25_loaded = store._bm25 is not None
    except Exception:
        pass
    with _cache_stats_lock:
        cache_snapshot = dict(_cache_stats)
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "kb_size": kb_size,
        "bm25_loaded": bm25_loaded,
        "llm_model": "claude-sonnet-4-6",
        "cache_stats": cache_snapshot,
        "uptime_seconds": uptime_seconds,
    }


@app.get("/app", response_class=HTMLResponse)
def ui():
    return (UI_DIR / "index.html").read_text()

@app.get("/manifest.json")
def manifest():
    from fastapi.responses import FileResponse
    return FileResponse(UI_DIR / "manifest.json", media_type="application/manifest+json")



# Shared instances
_store: NeuronStore | None = None
_engine: NeuronEngine | None = None


def get_store() -> NeuronStore:
    global _store
    if _store is None:
        _store = NeuronStore(CHROMA_DIR)
    return _store


def get_engine() -> NeuronEngine:
    global _engine
    if _engine is None:
        _engine = NeuronEngine()
    return _engine


async def _prewarm_caches():
    """Pre-warm in-memory caches for the most expensive endpoints.
    Called at startup and then every hour via background task.
    """
    import json
    from pathlib import Path
    from datetime import datetime, timedelta, date

    loop = asyncio.get_event_loop()

    async def _try(name, fn):
        try:
            result = await loop.run_in_executor(None, fn)
            logger.info("[PREWARM] %s OK", name)
            return result
        except Exception as e:
            logger.warning("[PREWARM] %s failed: %s", name, e)
            return None

    # Warm daily (file-cached, just reads JSON or calls LLM once)
    try:
        cache_path = Path.home() / ".neuron" / "daily_cache.json"
        today = date.today().isoformat()
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            if cached.get("date") == today:
                _mc_set("daily", cached, _TTL_DAILY)
                _record_cache_hit("daily")
                logger.info("[PREWARM] daily loaded from file cache")
            else:
                _record_cache_miss("daily")
                engine = get_engine()
                result = await loop.run_in_executor(None, engine.daily)
                result["date"] = today
                cache_path.parent.mkdir(exist_ok=True)
                cache_path.write_text(json.dumps(result))
                _mc_set("daily", result, _TTL_DAILY)
                _record_cache_hit("daily")
                logger.info("[PREWARM] daily regenerated")
        else:
            _record_cache_miss("daily")
            engine = get_engine()
            result = await loop.run_in_executor(None, engine.daily)
            result["date"] = today
            cache_path.parent.mkdir(exist_ok=True)
            cache_path.write_text(json.dumps(result))
            _mc_set("daily", result, _TTL_DAILY)
            _record_cache_hit("daily")
            logger.info("[PREWARM] daily generated (no file cache)")
    except Exception as e:
        logger.warning("[PREWARM] daily failed: %s", e)

    # Warm digest (most important — without this /today blocks on first load)
    try:
        cache_path = Path.home() / ".neuron" / "digest_cache.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(hours=12):
                _mc_set("digest", cached, _TTL_DAILY)
                logger.info("[PREWARM] digest loaded from file cache")
            else:
                _record_cache_miss("daily")
                engine = get_engine()
                result = await loop.run_in_executor(None, engine.digest)
                result["cached_at"] = datetime.now().isoformat()
                cache_path.write_text(json.dumps(result))
                _mc_set("digest", result, _TTL_DAILY)
                logger.info("[PREWARM] digest regenerated")
        else:
            engine = get_engine()
            result = await loop.run_in_executor(None, engine.digest)
            result["cached_at"] = datetime.now().isoformat()
            cache_path.parent.mkdir(exist_ok=True)
            cache_path.write_text(json.dumps(result))
            _mc_set("digest", result, _TTL_DAILY)
            logger.info("[PREWARM] digest generated (no file cache)")
    except Exception as e:
        logger.warning("[PREWARM] digest failed: %s", e)

    # Warm spark — load from file if exists (even yesterday's), regenerate async in background
    try:
        cache_path = Path.home() / ".neuron" / "sparks_cache.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            # Always load whatever cache exists so /today is fast
            _mc_set("spark_14_60", cached, _TTL_SPARK)
            if cached.get("cache_date") == date.today().isoformat():
                _record_cache_hit("spark")
                logger.info("[PREWARM] spark loaded from file cache")
            else:
                _record_cache_miss("spark")
                # Regenerate in background — don't block startup
                async def _regen_spark():
                    try:
                        eng = get_engine()
                        r = await asyncio.get_event_loop().run_in_executor(None, lambda: eng.spark(days_recent=14, days_old=60))
                        r["cached_at"] = datetime.now().isoformat()
                        r["cache_date"] = date.today().isoformat()
                        cache_path.parent.mkdir(exist_ok=True)
                        cache_path.write_text(json.dumps(r))
                        _mc_set("spark_14_60", r, _TTL_SPARK)
                        logger.info("[PREWARM] spark background regen done")
                    except Exception as be:
                        logger.warning("[PREWARM] spark background regen failed: %s", be)
                asyncio.create_task(_regen_spark())
                logger.info("[PREWARM] spark regen started in background")
    except Exception as e:
        logger.warning("[PREWARM] spark failed: %s", e)

    # Warm analogies
    try:
        cache_path = Path.home() / ".neuron" / "analogies_cache.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(hours=2):
                _mc_set("analogies", cached, _TTL_ANALOGIES)
                _record_cache_hit("analogies")
                logger.info("[PREWARM] analogies loaded from file cache")
            else:
                _record_cache_miss("analogies")
    except Exception as e:
        logger.warning("[PREWARM] analogies cache check failed: %s", e)

    # Warm cross-domain (random)
    try:
        cache_path = Path.home() / ".neuron" / "cross_domain_random.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(hours=2):
                _mc_set("cross_domain_random", cached, _TTL_CROSS)
                logger.info("[PREWARM] cross-domain loaded from file cache")
    except Exception as e:
        logger.warning("[PREWARM] cross-domain cache check failed: %s", e)

    # Warm suggestions
    try:
        cache_path = Path.home() / ".neuron" / "suggestions_cache.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(minutes=30):
                _mc_set("suggestions", cached, _TTL_SUGGESTIONS)
                logger.info("[PREWARM] suggestions loaded from file cache")
    except Exception as e:
        logger.warning("[PREWARM] suggestions cache check failed: %s", e)


async def _background_prewarm_loop():
    """Runs forever, re-warming caches every hour."""
    while True:
        await asyncio.sleep(3600)  # wait 1 hour between runs
        logger.info("[PREWARM] Hourly cache pre-warm starting")
        try:
            await _prewarm_caches()
        except Exception as e:
            logger.warning("[PREWARM] Hourly pre-warm error: %s", e)


async def _background_cache_stats_loop():
    """Logs cache hit/miss stats every hour."""
    while True:
        await asyncio.sleep(3600)
        with _cache_stats_lock:
            snapshot = dict(_cache_stats)
        logger.info("[CACHE STATS] %s", snapshot)


@app.on_event("startup")
async def startup_event():
    """Pre-warm NeuronStore, NeuronEngine, BM25, and endpoint caches at startup."""
    loop = asyncio.get_event_loop()
    try:
        store = await loop.run_in_executor(None, get_store)
        await loop.run_in_executor(None, get_engine)
        kb_size = store.count() if store else 0
        logger.info("Neuron startup: store and engine pre-warmed (KB size: %d chunks)", kb_size)
    except Exception as e:
        logger.warning("Neuron startup pre-warm failed: %s", e)

    # Pre-warm BM25 index in background (can take ~10s on large KBs)
    async def _warm_bm25():
        try:
            store = get_store()
            await loop.run_in_executor(None, store._ensure_bm25)
            logger.info("Neuron startup: BM25 pre-warmed")
        except Exception as e:
            logger.warning("Neuron startup BM25 pre-warm failed: %s", e)

    loop.create_task(_warm_bm25())

    # Pre-warm endpoint caches from existing file caches (fast, no LLM calls unless stale)
    loop.create_task(_prewarm_caches())

    # Start background hourly re-warming loop
    loop.create_task(_background_prewarm_loop())

    # Start background cache stats logger
    loop.create_task(_background_cache_stats_loop())

    # Create watchdog script if it doesn't exist
    _ensure_watchdog_script()


def _ensure_watchdog_script():
    """Create /tmp/neuron_watchdog.sh if it doesn't exist."""
    watchdog_path = Path("/tmp/neuron_watchdog.sh")
    if not watchdog_path.exists():
        watchdog_content = (
            "#!/bin/bash\n"
            "while true; do\n"
            "  if ! curl -sf http://localhost:7700/health > /dev/null 2>&1; then\n"
            "    cd ~/neuron && .venv/bin/python -m uvicorn neuron.api.server:app "
            "--port 7700 --host 0.0.0.0 --log-level warning "
            ">> /tmp/neuron.log 2>&1 &\n"
            "  fi\n"
            "  sleep 30\n"
            "done\n"
        )
        try:
            watchdog_path.write_text(watchdog_content)
            watchdog_path.chmod(0o755)
            logger.info("Neuron watchdog script created at %s", watchdog_path)
        except Exception as e:
            logger.warning("Could not create watchdog script: %s", e)
    else:
        logger.debug("Watchdog script already exists at %s", watchdog_path)


def _content_hash(text: str) -> str:
    import hashlib
    return hashlib.md5(text.strip().lower().encode()).hexdigest()[:16]


def dedupe_check(chunk: str, existing_chunks: list[str]) -> bool:
    """Return True if chunk has >90% word overlap with any existing chunk."""
    words_new = set(chunk.lower().split())
    if not words_new:
        return False
    for existing in existing_chunks:
        words_ex = set(existing.lower().split())
        if not words_ex:
            continue
        intersection = words_new & words_ex
        overlap = len(intersection) / max(len(words_new), len(words_ex))
        if overlap > 0.90:
            return True
    return False


_AUTO_TAG_RULES: list[tuple[str, list[str]]] = [
    ("science", ["physics", "chemistry", "biology", "quantum", "molecular", "genetic",
                 "neuroscience", "astronomy", "thermodynamics", "relativity", "atom",
                 "molecule", "enzyme", "protein", "evolution", "ecology"]),
    ("cs", ["algorithm", "code", "function", "software", "programming", "database",
            "machine learning", "neural network", "api", "compiler", "data structure",
            "complexity", "recursion", "object-oriented", "framework", "runtime"]),
    ("philosophy", ["ethics", "metaphysics", "consciousness", "epistemology", "ontology",
                    "phenomenology", "existential", "moral", "virtue", "determinism",
                    "free will", "rationalism", "empiricism", "dialectic", "socratic"]),
    ("history", ["century", "historical", "ancient", "empire", "civilization", "dynasty",
                 "medieval", "renaissance", "revolution", "colonialism", "archaeological",
                 "prehistoric", "antiquity", "feudal", "monarchy", "republic"]),
]


def _detect_auto_tags(text: str) -> list[str]:
    """Return category tags detected in text based on keyword rules."""
    text_lower = text.lower()
    tags: list[str] = []
    for tag, keywords in _AUTO_TAG_RULES:
        if any(kw in text_lower for kw in keywords):
            tags.append(tag)
    return tags


# In-memory log of recent ingestions for /ingest/stats
import collections as _collections
import threading as _threading
_ingest_log: _collections.deque = _collections.deque(maxlen=50)
_ingest_log_lock = _threading.Lock()


def _log_ingest(source: str, title: str, chunks: int, duplicates: int = 0):
    from datetime import datetime
    with _ingest_log_lock:
        _ingest_log.append({
            "source": source,
            "title": title,
            "chunks": chunks,
            "duplicates": duplicates,
            "timestamp": datetime.utcnow().isoformat(),
        })


def _chunk_and_store(docs: list[Document], store: NeuronStore):
    from datetime import datetime, timezone
    from ..cli import chunk_text, is_low_quality_chunk
    chunks, metadatas, ids = [], [], []
    seen: set[str] = set()
    seen_hashes: set[str] = set()
    duplicates = 0
    ingested_at = datetime.now(timezone.utc).isoformat()

    for doc in docs:
        prefix = f"[{doc.source.upper()}: {doc.title}]\n\n"
        for i, chunk in enumerate(chunk_text(doc.content)):
            cid = f"{doc.id}_c{i}"
            if cid in seen:
                continue
            if is_low_quality_chunk(chunk):
                continue
            # Content-hash deduplication: skip chunks whose text is identical to one already queued
            ch = _content_hash(chunk)
            if ch in seen_hashes:
                duplicates += 1
                logger.debug("Duplicate chunk skipped (hash %s) in doc '%s'", ch, doc.title)
                continue
            # Semantic near-duplicate check: skip chunks with >90% word overlap
            if dedupe_check(chunk, chunks):
                duplicates += 1
                logger.debug("Near-duplicate chunk skipped in doc '%s'", doc.title)
                continue
            seen_hashes.add(ch)
            seen.add(cid)
            chunks.append(prefix + chunk)
            metadata = {**doc.metadata}
            metadata.setdefault("created_at", ingested_at)
            metadata.setdefault("ingested_at", ingested_at)
            metadata["title"] = doc.title
            metadata["source"] = doc.source
            metadatas.append(metadata)
            ids.append(cid)

    if duplicates:
        logger.info("Deduplication: %d duplicate chunk(s) skipped across %d doc(s)", duplicates, len(docs))

    if chunks:
        store.upsert(chunks, metadatas, ids)

    # Log for /ingest/stats
    if docs:
        _log_ingest(
            source=docs[0].source,
            title=docs[0].title if len(docs) == 1 else f"{len(docs)} documents",
            chunks=len(chunks),
            duplicates=duplicates,
        )

    return len(chunks), len(docs)


# ── STATUS ─────────────────────────────────────────────────────────────────────

@app.get("/", dependencies=_auth)
def root():
    return {"name": "Neuron", "version": "0.1.0", "status": "running"}


@app.get("/status", dependencies=_auth)
def status():
    cached = _mc_get("status")
    if cached is not None:
        return cached

    from datetime import datetime, timedelta
    from ..retrieval.engine import _extract_date, _extract_ingest_date
    store = get_store()
    total = store.count()
    # Fetch only metadatas (no documents/embeddings) — fast even for 130k+ docs
    breakdown: dict[str, int] = {}
    source_dates: list[str] = []
    ingest_dates: list[str] = []
    try:
        result = store.collection.get(include=["metadatas"])
        for meta in result["metadatas"]:
            src = meta.get("source", "")
            if src:
                breakdown[src] = breakdown.get(src, 0) + 1
            d = _extract_date(meta)
            if d:
                source_dates.append(d)
            ingested = _extract_ingest_date(meta)
            if ingested:
                ingest_dates.append(ingested)
    except Exception:
        pass

    source_dates_sorted = sorted(source_dates)
    ingest_dates_sorted = sorted(ingest_dates)
    oldest_source = source_dates_sorted[0] if source_dates_sorted else None
    oldest_ingest = ingest_dates_sorted[0] if ingest_dates_sorted else None
    newest_ingest = ingest_dates_sorted[-1] if ingest_dates_sorted else None

    knowledge_age_days: int | None = None
    if oldest_ingest:
        try:
            knowledge_age_days = (datetime.now() - datetime.fromisoformat(oldest_ingest)).days
        except Exception:
            pass

    # Top 5 sources by chunk count
    top_sources = sorted(breakdown.items(), key=lambda x: -x[1])[:5]

    # Recent topics: pull 3 representative tags from recent content
    recent_topics: list[str] = []
    try:
        cutoff = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
        topic_seeds = [
            "main topic subject area this week",
            "key concept learned recently",
            "project work meeting discussion",
        ]
        seen: set[str] = set()
        for seed in topic_seeds:
            res = store.search(seed, n_results=6)
            for meta in res["metadatas"][0]:
                d = _extract_date(meta)
                if d and d >= cutoff:
                    title = meta.get("title", "").strip()
                    src = meta.get("source", "")
                    tag = title.split(":")[0].strip() if title else src
                    if tag and tag not in seen:
                        seen.add(tag)
                        recent_topics.append(tag)
                        break
            if len(recent_topics) >= 3:
                break
    except Exception:
        pass

    payload = {
        "total_chunks": total,
        "sources": breakdown,
        "knowledge_age_days": knowledge_age_days,
        "last_ingest_date": newest_ingest,
        "ingest_metadata_coverage": round((len(ingest_dates) / total), 4) if total else 0.0,
        "oldest_source_date": oldest_source,
        "top_sources": [{"source": s, "chunks": c} for s, c in top_sources],
        "recent_topics": recent_topics,
    }
    _mc_set("status", payload, 300)
    return payload


@app.get("/stats", dependencies=_auth)
def stats():
    """Richer stats: chunk counts, source breakdown, recent activity, oldest/newest dates."""
    from datetime import datetime, timedelta
    from ..retrieval.engine import _extract_date
    store = get_store()
    total = store.count()
    if total == 0:
        return {
            "total_chunks": 0,
            "source_breakdown": {},
            "recent_activity": 0,
            "oldest_item_date": None,
            "newest_item_date": None,
        }
    try:
        result = store.collection.get(include=["metadatas"])
        source_breakdown: dict[str, int] = {}
        dates: list[str] = []
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        recent_count = 0
        for meta in result["metadatas"]:
            src = meta.get("source", "unknown")
            source_breakdown[src] = source_breakdown.get(src, 0) + 1
            d = _extract_date(meta)
            if d:
                dates.append(d)
                if d >= week_ago:
                    recent_count += 1
        dates.sort()
        return {
            "total_chunks": total,
            "source_breakdown": dict(sorted(source_breakdown.items(), key=lambda x: -x[1])),
            "recent_activity": recent_count,
            "oldest_item_date": dates[0] if dates else None,
            "newest_item_date": dates[-1] if dates else None,
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Stats unavailable: {e}")


# ── ADMIN ──────────────────────────────────────────────────────────────────────

@app.post("/admin/prune-noise", dependencies=_auth)
def prune_noise():
    """Remove low-quality / boilerplate chunks from the knowledge base.

    Scans all chunks and deletes:
    1. Chunks shorter than 80 characters (existing is_low_quality_chunk logic)
    2. Chunks that are just filenames (e.g. "document.pdf\n")
    3. Chunks from the "folder" source (directory listing noise)
    4. Email/calendar boilerplate (existing logic)
    Invalidates the BM25 cache so the next search rebuilds it.
    Returns { removed, remaining, breakdown }.
    """
    import re as _re
    from ..cli import is_low_quality_chunk

    store = get_store()
    try:
        result = store.collection.get(include=["documents", "metadatas"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not fetch collection: {e}")

    ids_to_remove: list[str] = []
    reasons: dict[str, int] = {"low_quality": 0, "filename_only": 0, "folder_source": 0}

    # Regex: chunk body is just a filename (optionally with path) — e.g. "document.pdf\n" or "notes/hw1.docx"
    _filename_re = _re.compile(
        r'^[\w\-\. /]+\.(pdf|docx|pptx|xlsx|txt|py|java|cpp|c|h|js|ts|md|csv|json|xml|zip|tar|gz|ppt|doc)\s*$',
        _re.IGNORECASE,
    )

    for doc_id, doc, meta in zip(result["ids"], result["documents"], result["metadatas"]):
        source = (meta or {}).get("source", "")

        # Rule 3: folder source = directory listing, pure noise
        if source == "folder":
            ids_to_remove.append(doc_id)
            reasons["folder_source"] += 1
            continue

        # Strip the "[SOURCE: Title]\n\n" prefix before quality-checking
        body = doc
        if "\n\n" in doc:
            body = doc.split("\n\n", 1)[1]

        # Rule 1: existing low-quality logic (short, boilerplate, etc.)
        if is_low_quality_chunk(body):
            ids_to_remove.append(doc_id)
            reasons["low_quality"] += 1
            continue

        # Rule 2: chunk body is just a filename
        if _filename_re.match(body.strip()):
            ids_to_remove.append(doc_id)
            reasons["filename_only"] += 1
            continue

    if ids_to_remove:
        # Delete in batches to avoid ChromaDB request size limits
        batch_size = 5000
        for i in range(0, len(ids_to_remove), batch_size):
            store.collection.delete(ids=ids_to_remove[i : i + batch_size])
        # Invalidate BM25 cache — forces rebuild on next search
        store._bm25 = None
        try:
            store._bm25_cache_path().unlink(missing_ok=True)
        except Exception:
            pass

    remaining = store.count()
    return {"removed": len(ids_to_remove), "remaining": remaining, "breakdown": reasons}


@app.get("/admin/memory-stats", dependencies=_auth)
def admin_memory_stats():
    """Report BM25 RAM usage, in-memory cache sizes, and process memory. For debugging/monitoring."""
    import json as _json
    import resource as _resource

    stats: dict = {}

    # BM25 memory estimate
    try:
        store = get_store()
        bm25_loaded = store._bm25 is not None
        bm25_doc_count = 0
        bm25_size_mb = 0.0
        if bm25_loaded:
            bm25_doc_count = getattr(store._bm25, "corpus_size",
                             len(getattr(store._bm25, "corpus", [])))
            # Rough estimate: each doc averages ~200 bytes in BM25 index
            bm25_size_mb = round(bm25_doc_count * 200 / 1024 / 1024, 2)
        stats["bm25"] = {
            "loaded": bm25_loaded,
            "doc_count": bm25_doc_count,
            "estimated_mb": bm25_size_mb,
        }
    except Exception as e:
        stats["bm25"] = {"error": str(e)}

    # In-memory cache sizes
    try:
        cache_keys = list(_mem_cache.keys())
        total_cache_bytes = 0
        for k, v in list(_mem_cache.items()):
            try:
                total_cache_bytes += sys.getsizeof(_json.dumps(v.get("data", "")))
            except Exception:
                pass
        stats["mem_cache"] = {
            "entry_count": len(cache_keys),
            "keys": cache_keys,
            "estimated_mb": round(total_cache_bytes / 1024 / 1024, 3),
        }
    except Exception as e:
        stats["mem_cache"] = {"error": str(e)}

    # Rate limit table size
    with _rate_limit_lock:
        stats["rate_limit_table"] = {"ip_count": len(_rate_limit)}

    # Process memory via resource module
    try:
        rusage = _resource.getrusage(_resource.RUSAGE_SELF)
        # macOS: ru_maxrss is bytes; Linux: kilobytes
        if sys.platform == "darwin":
            rss_mb = round(rusage.ru_maxrss / 1024 / 1024, 1)
        else:
            rss_mb = round(rusage.ru_maxrss / 1024, 1)
        stats["process_memory_mb"] = rss_mb
    except Exception:
        stats["process_memory_mb"] = None

    # KB chunk count
    try:
        stats["kb_chunks"] = get_store().count()
    except Exception:
        stats["kb_chunks"] = None

    stats["uptime_seconds"] = int(time.monotonic() - _SERVER_START_TIME)
    return stats


@app.post("/admin/backfill-ingest-metadata", dependencies=_auth)
def backfill_ingest_metadata(dry_run: bool = False):
    """Backfill missing created_at / ingested_at metadata on existing chunks.

    Priority:
    1. Keep existing created_at / ingested_at if present.
    2. Fall back to source-specific date fields.
    3. Fall back to "now" if nothing date-like exists.
    """
    from datetime import datetime, timezone

    store = get_store()
    try:
        result = store.collection.get(include=["documents", "metadatas"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not fetch collection: {e}")

    ids: list[str] = result.get("ids") or []
    documents: list[str] = result.get("documents") or []
    metadatas: list[dict] = result.get("metadatas") or []
    now_iso = datetime.now(timezone.utc).isoformat()

    updated_docs: list[str] = []
    updated_metas: list[dict] = []
    updated_ids: list[str] = []
    updated = 0
    unchanged = 0

    for doc_id, doc, meta in zip(ids, documents, metadatas):
        meta = dict(meta or {})
        current_created = str(meta.get("created_at", "") or "").strip()
        current_ingested = str(meta.get("ingested_at", "") or "").strip()
        if current_created and current_ingested:
            unchanged += 1
            continue

        fallback = (
            current_created
            or current_ingested
            or str(meta.get("date", "") or "").strip()
            or str(meta.get("source_date", "") or "").strip()
            or str(meta.get("published_at", "") or "").strip()
            or str(meta.get("updated_at", "") or "").strip()
            or now_iso
        )
        meta.setdefault("created_at", fallback)
        meta.setdefault("ingested_at", fallback)

        updated += 1
        if not dry_run:
            updated_docs.append(doc)
            updated_metas.append(meta)
            updated_ids.append(doc_id)

    if not dry_run and updated_ids:
        store.upsert(updated_docs, updated_metas, updated_ids)
        _mc_delete_prefix("library_")
        _mc_delete("today")

    return {
        "ok": True,
        "dry_run": dry_run,
        "updated": updated,
        "unchanged": unchanged,
        "total": len(ids),
    }


# ── INGEST ─────────────────────────────────────────────────────────────────────

class IngestURLRequest(BaseModel):
    url: str


class IngestTextRequest(BaseModel):
    text: str
    title: str | None = None
    source: str = "note"
    author: str | None = None  # used when source="book" or source="kindle"


@app.post("/ingest/url", dependencies=_auth)
def ingest_url(req: IngestURLRequest):
    """Ingest a web page — called by the browser extension."""
    from ..ingestion.web import WebIngester
    try:
        docs = WebIngester().ingest(req.url)
        store = get_store()
        chunks, n_docs = _chunk_and_store(docs, store)
        tag = docs[0].metadata.get("tag", "web") if docs else "web"
        return {
            "ok": True,
            "chunks": chunks,
            "documents": n_docs,
            "title": docs[0].title if docs else req.url,
            "tag": tag,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/ingest/text", dependencies=_auth)
def ingest_text(req: IngestTextRequest):
    """Ingest a note, idea, or any raw text.

    When source='book' or source='kindle', the text is parsed as highlights
    (separated by '---' or double newlines) and each highlight is stored as a
    separate Document tagged with book title, author, and optional chapter.
    """
    import uuid
    import re as _re
    from datetime import datetime

    if req.source in ("book", "kindle"):
        raw_title = req.title or f"Book — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        author = req.author or ""
        text = req.text

        # Split on '---' separators or double newlines
        if "---" in text:
            raw_highlights = [h.strip() for h in text.split("---") if h.strip()]
        else:
            raw_highlights = [h.strip() for h in _re.split(r"\n{2,}", text) if h.strip()]

        if not raw_highlights:
            raw_highlights = [text.strip()]

        docs_to_store = []
        for i, highlight in enumerate(raw_highlights):
            # Detect optional chapter header like "Chapter 3:" or "Part II"
            chapter = ""
            ch_m = _re.match(r"^(Chapter\s+\d+|Part\s+[IVXLC\d]+)[:\s]", highlight, _re.IGNORECASE)
            if ch_m:
                chapter = ch_m.group(1)

            auto_tags = _detect_auto_tags(highlight)
            docs_to_store.append(Document(
                id=f"book_{uuid.uuid4().hex[:8]}_h{i}",
                content=highlight,
                source="kindle",
                title=raw_title,
                metadata={
                    "type": "book_highlights",
                    "book": raw_title,
                    "author": author,
                    "chapter": chapter,
                    "created_at": datetime.now().isoformat(),
                    "auto_tags": ",".join(auto_tags),
                },
            ))

        store = get_store()
        chunks, n_docs = _chunk_and_store(docs_to_store, store)
        # Invalidate caches that depend on ingested content
        _mc_delete("today")
        _mc_delete_prefix("library_")
        all_tags = list({t for doc in docs_to_store for t in _detect_auto_tags(doc.content)})
        summary = f"Ingested {len(raw_highlights)} highlight(s) from '{raw_title}'."
        return {
            "ok": True,
            "id": docs_to_store[0].id if docs_to_store else None,
            "chunks_created": chunks,
            "documents": n_docs,
            "highlights_parsed": len(raw_highlights),
            "book": raw_title,
            "author": author,
            "topics_detected": all_tags,
            "summary": summary,
        }

    # Default: plain text note
    auto_tags = _detect_auto_tags(req.text)
    doc_id = f"{req.source}_{uuid.uuid4().hex[:8]}"
    doc = Document(
        id=doc_id,
        content=req.text,
        source=req.source,
        title=req.title or f"Note — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        metadata={
            "type": req.source,
            "created_at": datetime.now().isoformat(),
            "auto_tags": ",".join(auto_tags),
        },
    )
    store = get_store()
    chunks, n_docs = _chunk_and_store([doc], store)
    # Invalidate caches that depend on ingested content
    _mc_delete("today")
    _mc_delete_prefix("library_")
    # Build a short summary from the first 200 chars of text
    text_preview = req.text.strip()[:200].replace("\n", " ")
    summary = f"Ingested note '{doc.title}': {text_preview}{'...' if len(req.text) > 200 else ''}"
    return {
        "ok": True,
        "id": doc_id,
        "chunks_created": chunks,
        "documents": n_docs,
        "topics_detected": auto_tags,
        "summary": summary,
    }


@app.post("/ingest/file", dependencies=_auth)
async def ingest_file(file: UploadFile = File(...)):
    """Ingest an uploaded file (PDF, txt, md, docx)."""
    from ..ingestion.file import FileIngester
    suffix = Path(file.filename or "upload.txt").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        docs = FileIngester().ingest(tmp_path)
        # Override title with original filename
        for doc in docs:
            doc.title = file.filename or doc.title
        store = get_store()
        chunks, n_docs = _chunk_and_store(docs, store)
        return {"ok": True, "chunks": chunks, "documents": n_docs, "title": file.filename}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.post("/ingest/goodnotes", dependencies=_auth)
def ingest_goodnotes_api(path: str | None = None):
    """Ingest GoodNotes notebooks from iCloud or a given folder path."""
    from ..ingestion.goodnotes import GoodNotesIngester
    try:
        docs = GoodNotesIngester().ingest(path or None)
        store = get_store()
        chunks, n_docs = _chunk_and_store(docs, store)
        return {"ok": True, "chunks": chunks, "documents": n_docs,
                "message": f"Indexed {n_docs} notebook(s), {chunks} chunks" if n_docs else "No text found. Export from GoodNotes as PDF with text recognition enabled."}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest/voice", dependencies=_auth)
async def ingest_voice(
    file: UploadFile = File(default=None),
    text: str = Form(default=None),
    title: str = Form(default=None),
    source: str = Form(default="voice_memo"),
    duration_seconds: float = Form(default=0),
):
    """Ingest a voice memo. Accepts either:
    - An audio file (webm, mp4, m4a, wav, mp3) — transcribed via OpenAI Whisper API then post-processed by Claude.
    - Plain text (pre-transcribed, e.g. from iOS on-device transcription).
    """
    import uuid
    import os as _os
    from datetime import datetime

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d %H:%M")
    date_iso = now.date().isoformat()

    # ── 1. Get raw transcript ────────────────────────────────────────────────
    raw_transcript: str = ""

    if file is not None and file.filename:
        audio_bytes = await file.read()
        # Determine extension for Whisper (must be a supported type)
        fname = file.filename or "audio.webm"
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "webm"
        supported = {"webm", "mp4", "m4a", "wav", "mp3", "mpeg", "mpga", "ogg"}
        if ext not in supported:
            ext = "webm"  # browser MediaRecorder default

        # Try OpenAI Whisper API first
        openai_key = _os.environ.get("OPENAI_API_KEY", "").strip()
        if openai_key:
            try:
                from openai import OpenAI as _OAI
                oai = _OAI(api_key=openai_key)

                import io as _io
                audio_file = _io.BytesIO(audio_bytes)
                audio_file.name = f"audio.{ext}"  # openai client reads .name for MIME

                transcription = oai.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text",
                )
                raw_transcript = str(transcription).strip()
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Whisper API transcription failed: {e}")
        else:
            # Fallback: local Whisper via subprocess (pip install openai-whisper)
            import tempfile as _tempfile
            import subprocess as _subprocess
            try:
                with _tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                    tmp.write(audio_bytes)
                    tmp_path = tmp.name
                try:
                    result = _subprocess.run(
                        ["whisper", tmp_path, "--model", "base", "--output_format", "txt",
                         "--output_dir", _os.path.dirname(tmp_path)],
                        capture_output=True, text=True, timeout=120,
                    )
                    txt_path = tmp_path.rsplit(".", 1)[0] + ".txt"
                    if _os.path.exists(txt_path):
                        with open(txt_path) as f:
                            raw_transcript = f.read().strip()
                        _os.unlink(txt_path)
                    else:
                        raise RuntimeError(result.stderr or "whisper produced no output")
                finally:
                    try:
                        _os.unlink(tmp_path)
                    except Exception:
                        pass
            except FileNotFoundError:
                raise HTTPException(
                    status_code=503,
                    detail="OPENAI_API_KEY not set and local 'whisper' CLI not found. "
                           "Either set OPENAI_API_KEY or install: pip install openai-whisper. "
                           "Alternatively, submit pre-transcribed text instead.",
                )
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Local Whisper transcription failed: {e}")

    elif text:
        raw_transcript = text.strip()

    if not raw_transcript:
        raise HTTPException(status_code=400, detail="No audio file or text provided, or transcript is empty.")

    # ── 2. Clean up filler words ────────────────────────────────────────────
    import re as _re_voice
    _filler_pattern = _re_voice.compile(
        r'\b(um+|uh+|like|you know|you know what i mean|i mean|sort of|kind of|basically|literally|actually|right\?)\b',
        _re_voice.IGNORECASE,
    )
    cleaned_raw = _filler_pattern.sub('', raw_transcript)
    # Collapse multiple spaces left by filler removal
    cleaned_raw = _re_voice.sub(r'  +', ' ', cleaned_raw).strip()

    # ── 3. Post-process with Claude ─────────────────────────────────────────
    cleaned_transcript = cleaned_raw  # fallback if no LLM key
    action_items: list[str] = []
    key_concepts: list[str] = []
    try:
        engine = get_engine()
        post_process_prompt = (
            "The following is a voice transcript. Clean it up (fix incomplete sentences) "
            "but preserve the meaning exactly. Then return a JSON object with these keys:\n"
            "- \"cleaned\": the cleaned transcript as a string\n"
            "- \"action_items\": list of action items mentioned (strings)\n"
            "- \"key_concepts\": list of key concepts or topics discussed (strings)\n"
            "Return ONLY the JSON object, no extra text.\n\n"
            f"Transcript: {cleaned_raw}"
        )
        llm_response = engine._chat(post_process_prompt, max_tokens=2048)
        import json as _json_voice
        # Try to parse JSON from LLM response
        try:
            # Strip markdown code fences if present
            _raw_json = llm_response.strip()
            if _raw_json.startswith("```"):
                _raw_json = _re_voice.sub(r"^```[a-z]*\n?", "", _raw_json)
                _raw_json = _re_voice.sub(r"\n?```$", "", _raw_json.strip())
            parsed = _json_voice.loads(_raw_json)
            cleaned_transcript = parsed.get("cleaned", cleaned_raw)
            action_items = parsed.get("action_items", [])
            key_concepts = parsed.get("key_concepts", [])
        except Exception:
            # If JSON parse fails, use the whole response as cleaned text
            cleaned_transcript = llm_response
    except Exception:
        pass  # If LLM fails, fall back to filler-cleaned transcript

    # ── 4. Auto-generate title from Claude if not provided ──────────────────
    doc_title = title.strip() if title and title.strip() else ""
    if not doc_title:
        try:
            engine = get_engine()
            title_prompt = (
                "Generate a concise, descriptive title (5-10 words) for this voice memo. "
                "Return ONLY the title text, nothing else.\n\n"
                f"Content: {cleaned_transcript[:500]}"
            )
            generated_title = engine._chat(title_prompt, max_tokens=50).strip().strip('"').strip("'")
            doc_title = generated_title if generated_title else f"Voice Memo — {today_str}"
        except Exception:
            doc_title = f"Voice Memo — {today_str}"

    # ── 5. Ingest as document ────────────────────────────────────────────────
    auto_tags = _detect_auto_tags(cleaned_transcript)
    doc = Document(
        id=f"voice_memo_{uuid.uuid4().hex[:8]}",
        content=cleaned_transcript,
        source="voice_memo",
        title=doc_title,
        metadata={
            "type": "voice_memo",
            "created_at": now.isoformat(),
            "date": date_iso,
            "duration_seconds": duration_seconds,
            "raw_transcript": raw_transcript,
            "action_items": "; ".join(action_items),
            "key_concepts": "; ".join(key_concepts),
            "auto_tags": ",".join(auto_tags),
        },
    )
    store = get_store()
    chunks, n_docs = _chunk_and_store([doc], store)
    return {
        "ok": True,
        "chunks": chunks,
        "documents": n_docs,
        "title": doc.title,
        "transcript": raw_transcript,
        "cleaned_transcript": cleaned_transcript,
        "action_items": action_items,
        "key_concepts": key_concepts,
        "topics_detected": auto_tags,
    }


@app.get("/daily/voice-summary", dependencies=_auth)
def daily_voice_summary():
    """Return a structured, audio-friendly daily briefing.

    Combines today's voice memos with calendar events and knowledge base data
    to produce a short spoken-word briefing (under 200 words) plus a full summary.
    """
    from datetime import date, datetime as _dt
    import os as _os

    today = date.today()
    today_iso = today.isoformat()
    day_name = today.strftime("%A")
    month_day = today.strftime("%B %-d")  # e.g. "March 9"

    # Fetch the user's name for greeting
    user_name = _os.environ.get("NEURON_USER_NAME", "").strip()

    # ── 1. Fetch today's voice memo chunks ─────────────────────────────────────
    store = get_store()
    docs = []
    try:
        result = store.collection.get(
            where={"$and": [{"source": {"$eq": "voice_memo"}}, {"date": {"$eq": today_iso}}]},
            include=["documents", "metadatas"],
        )
        docs = result.get("documents") or []
    except Exception:
        try:
            result = store.collection.get(
                where={"source": {"$eq": "voice_memo"}},
                include=["documents", "metadatas"],
            )
            raw_docs = result.get("documents") or []
            raw_metas = result.get("metadatas") or []
            docs = [
                d for d, m in zip(raw_docs, raw_metas)
                if m.get("date") == today_iso or m.get("created_at", "").startswith(today_iso)
            ]
        except Exception:
            docs = []

    # ── 2. Fetch upcoming exam/quiz events from the knowledge base ─────────────
    exam_events: list[str] = []
    try:
        from datetime import timedelta
        week_later = (today + timedelta(days=7)).isoformat()
        ev_result = store.collection.get(
            where={"source": {"$eq": "calendar"}},
            include=["documents", "metadatas"],
        )
        ev_docs = ev_result.get("documents") or []
        ev_metas = ev_result.get("metadatas") or []
        for d, m in zip(ev_docs, ev_metas):
            title = m.get("title", d[:80])
            event_date = m.get("date", "")
            low = title.lower()
            if any(k in low for k in ("exam", "midterm", "quiz", "test", "final")):
                if today_iso <= event_date <= week_later:
                    exam_events.append(f"{title} on {event_date}")
    except Exception:
        pass

    # ── 3. Build the briefing via LLM ──────────────────────────────────────────
    name_part = f" {user_name}" if user_name else ""
    greeting_line = f"Good morning{name_part}."
    date_line = f"Today is {day_name}, {month_day}."

    combined_memos = "\n\n---\n\n".join(docs) if docs else ""
    exam_section = "\n".join(f"- {e}" for e in exam_events) if exam_events else ""

    briefing = ""
    summary = ""
    try:
        engine = get_engine()

        # Generate the short spoken briefing (≤200 words)
        briefing_prompt = (
            f"You are a personal AI assistant giving a morning audio briefing.\n"
            f"Today is {day_name}, {month_day}.\n"
            + (f"The user's name is {user_name}.\n" if user_name else "")
            + (f"\nToday's voice memos / notes:\n{combined_memos}\n" if combined_memos else "")
            + (f"\nUpcoming exams this week:\n{exam_section}\n" if exam_section else "")
            + "\nWrite a concise spoken briefing in plain prose (no markdown, no bullet points, no emojis).\n"
            "Structure:\n"
            "1. A warm greeting using the user's name and today's date.\n"
            "2. Top 3 things to know or focus on today (based on memos or just motivational if no memos).\n"
            "3. Any upcoming exams in the next week (skip this section if none).\n"
            "4. One learning insight or motivational thought.\n"
            "Keep it under 200 words. Write it to be read aloud naturally."
        )
        briefing = engine._chat(briefing_prompt, max_tokens=400)

        # Generate a longer written summary if memos exist
        if combined_memos:
            summary_prompt = (
                f"Below are notes from voice memos recorded today ({today_iso}).\n"
                "Synthesize them into a clear, well-structured 'Today I Learned' summary.\n"
                "Group related ideas, highlight the most important concepts, surface any open questions or action items.\n"
                "Write it as if you are summarizing the day's learning for a student's personal knowledge base.\n\n"
                f"Notes:\n{combined_memos}"
            )
            summary = engine._chat(summary_prompt, max_tokens=1500)
        else:
            summary = ""
    except Exception:
        # Fallback: assemble a basic briefing without LLM
        parts = [greeting_line, date_line]
        if exam_events:
            parts.append("Upcoming exams: " + "; ".join(exam_events[:3]) + ".")
        if combined_memos:
            parts.append("Here is what you recorded today: " + combined_memos[:300])
        briefing = " ".join(parts)
        summary = combined_memos

    if not briefing:
        briefing = f"{greeting_line} {date_line} No voice memos recorded today yet. Record one to get a personalized briefing."

    return {
        "ok": True,
        "date": today_iso,
        "day": day_name,
        "briefing": briefing,          # Short spoken text (≤200 words) — use for TTS
        "summary": summary,            # Full written summary of today's memos
        "memo_count": len(docs),
        "exam_events": exam_events,
        "message": "" if docs else "No voice memos found for today. Record one to get a personalized briefing.",
    }


@app.post("/ingest/youtube", dependencies=_auth)
def ingest_youtube(req: IngestURLRequest):
    from ..ingestion.youtube import YouTubeIngester
    try:
        docs = YouTubeIngester().ingest(req.url)
        store = get_store()
        chunks, n_docs = _chunk_and_store(docs, store)
        return {"ok": True, "chunks": chunks, "documents": n_docs, "title": docs[0].title if docs else req.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/ingest/stats", dependencies=_auth)
def ingest_stats():
    """Ingest statistics: chunks by source type, recent ingestions, and today/this-week counts."""
    from datetime import datetime, timedelta

    store = get_store()
    total = store.count()

    # Source breakdown from the store
    source_breakdown: dict[str, int] = {}
    today_str = datetime.utcnow().date().isoformat()
    week_ago_str = (datetime.utcnow() - timedelta(days=7)).date().isoformat()
    chunks_today = 0
    chunks_this_week = 0

    try:
        result = store.collection.get(include=["metadatas"])
        for meta in result["metadatas"]:
            src = meta.get("source", "unknown")
            source_breakdown[src] = source_breakdown.get(src, 0) + 1
            # Count today/week using created_at or date fields
            for date_field in ("created_at", "date"):
                d = meta.get(date_field, "")
                if d:
                    d_str = d[:10]  # YYYY-MM-DD prefix
                    if d_str == today_str:
                        chunks_today += 1
                    if d_str >= week_ago_str:
                        chunks_this_week += 1
                    break
    except Exception:
        pass

    # Recent ingestions from in-memory log
    with _ingest_log_lock:
        recent = list(_ingest_log)[-10:]
    recent.reverse()  # most recent first

    return {
        "ok": True,
        "total_chunks": total,
        "chunks_by_source": dict(sorted(source_breakdown.items(), key=lambda x: -x[1])),
        "chunks_added_today": chunks_today,
        "chunks_added_this_week": chunks_this_week,
        "recent_ingestions": recent,
    }


# ── RETRIEVAL ──────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    q: str
    n_results: int = 15


_TTL_ASK = 30 * 60  # 30 minutes for /ask cache

# Date-reference keywords that make a query time-sensitive — skip caching for these.
_DATE_KEYWORDS = re.compile(
    r'\b(today|tonight|yesterday|tomorrow|this week|last week|next week|'
    r'this month|last month|right now|currently|recently|latest|just|'
    r'monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',
    re.IGNORECASE,
)


@app.post("/ask", dependencies=_auth)
async def ask(req: QueryRequest, request: Request):
    import re as _re, json as _json, hashlib as _hashlib
    loop = asyncio.get_event_loop()

    # Rate limit: 5 requests/min per IP for this expensive endpoint
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip, "/ask", max_per_minute=5):
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded", "detail": "Max 5 requests per minute for /ask. Please wait."},
        )

    # ── Answer cache (30-min TTL, skip if query references current date/time) ──
    _q_norm = req.q.lower().strip()
    _skip_cache = bool(_DATE_KEYWORDS.search(_q_norm))
    _cache_key = "ask:" + _hashlib.sha256(_q_norm.encode()).hexdigest()
    if not _skip_cache:
        _cached_answer = _mc_get(_cache_key)
        if _cached_answer is not None:
            logger.info("[CACHE HIT] /ask key=%s", _cache_key[:20])
            return _cached_answer

    try:
        engine = get_engine()
        # Run the main ask() call in a thread with a 60s timeout
        _t0_ask = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: engine.ask(req.q, n_results=req.n_results)),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[TIMEOUT] /ask timed out after 60s for query: %s", req.q[:100])
            return JSONResponse(
                status_code=200,
                content={
                    "answer": "The response took too long to generate (>60s). The knowledge base is likely very large. Try a more specific question or use /ask/quick for a faster response.",
                    "sources": [],
                    "timeout": True,
                },
            )
        logger.info("[TIMING] /ask LLM call: %.1fs", time.perf_counter() - _t0_ask)

        # Run practice_hook and related_questions fully in parallel via asyncio.gather
        answer_text = result.get("answer", "")
        if answer_text and len(answer_text) > 100:
            try:
                def _make_hook():
                    return engine._chat(
                        f"You are a learning science expert.\n"
                        f"Based on this answer, generate 1 retrieval practice question about the MOST IMPORTANT concept.\n"
                        f"The question should be ONE LEVEL HARDER than trivial — require understanding, not just recall.\n"
                        f"It should force the student to generate an answer (generation effect).\n"
                        f"Always include a 'why does this matter?' angle (elaborative interrogation).\n\n"
                        f"ANSWER:\n{answer_text[:1500]}\n\n"
                        f"Return ONLY valid JSON (no markdown):\n"
                        '{{"question": "...", "concept": "the key concept being tested", "why_it_matters": "1 sentence"}}',
                        max_tokens=200,
                        model="claude-haiku-4-5-20251001",
                    )

                def _make_related_questions():
                    try:
                        rq_prompt = (
                            f"Based on this question and answer, generate exactly 3 short, specific follow-up questions "
                            f"that a curious person would naturally want to ask next. "
                            f"Each question should be under 12 words, concrete, and directly related to the content.\n\n"
                            f"Question: {req.q}\n"
                            f"Answer snippet: {answer_text[:500]}\n\n"
                            f"Return ONLY a JSON array of 3 strings, no markdown, no extra text:\n"
                            '["Question 1?", "Question 2?", "Question 3?"]'
                        )
                        raw = engine._chat(rq_prompt, max_tokens=200, model="claude-haiku-4-5-20251001")
                        m2 = _re.search(r'\[[\s\S]*?\]', raw)
                        if m2:
                            parsed = _json.loads(m2.group(0))
                            if isinstance(parsed, list):
                                return [str(q) for q in parsed[:4] if q]
                    except Exception:
                        pass
                    return []

                # Run both fully concurrently with asyncio.gather
                raw_hook, related_questions = await asyncio.gather(
                    loop.run_in_executor(None, _make_hook),
                    loop.run_in_executor(None, _make_related_questions),
                    return_exceptions=True,
                )

                if not isinstance(raw_hook, Exception):
                    m = _re.search(r'\{[\s\S]*\}', raw_hook)
                    if m:
                        hook = _json.loads(m.group(0))
                        if isinstance(hook, dict) and hook.get("question"):
                            result["practice_hook"] = hook
                if not isinstance(related_questions, Exception) and related_questions:
                    result["related_questions"] = related_questions
            except Exception:
                pass  # non-fatal

        # Store in cache (skip if time-sensitive query)
        if not _skip_cache and result.get("answer"):
            _mc_set(_cache_key, result, _TTL_ASK)

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ask/stream", dependencies=_auth)
def ask_stream(req: QueryRequest):
    """Streaming version of /ask — returns SSE with token-by-token answer."""
    import json as _json
    import os as _os

    engine = get_engine()

    def generate():
        try:
            # Run retrieval (fast)
            from ..retrieval.engine import _build_numbered_context, _dedup_by_content
            from datetime import datetime
            _now = datetime.now()
            today = _now.strftime("%A, %B ") + str(_now.day) + _now.strftime(", %Y")

            queries = engine._expand_query(req.q)
            scored = engine._multi_search(queries, n_candidates=200)

            # Dedup by title first (fast)
            seen_title_keys: set[str] = set()
            deduped = []
            for item in scored:
                meta = item[2]
                key = f"{meta.get('source', '')}::{meta.get('title', '')}"
                if key not in seen_title_keys:
                    seen_title_keys.add(key)
                    deduped.append(item)

            # Content-similarity dedup: drop near-duplicate chunks (>80% word overlap)
            deduped = _dedup_by_content(deduped, threshold=0.80)

            scored = deduped[:req.n_results]

            if not scored:
                yield f"data: {_json.dumps({'type': 'done', 'answer': 'Nothing relevant found.', 'sources': []})}\n\n"
                return

            docs = [x[1] for x in scored]
            metas = [x[2] for x in scored]
            context, sources = _build_numbered_context(docs, metas)
            upcoming_block = engine._upcoming_summary(days=14)
            upcoming_section = f"\n\n{upcoming_block}" if upcoming_block else ""

            # Send sources immediately so UI can render them while streaming text
            yield f"data: {_json.dumps({'type': 'sources', 'sources': sources})}\n\n"

            # Build a clean "Sources:" line for the end of the response
            source_names = ", ".join(
                s["title"] or s["source"]
                for s in sources[:10]
                if s.get("title") or s.get("source")
            )

            prompt = (
                f"You are Neuron — a second brain built from Ralph's actual notes, meetings, courses, and work.\n"
                f"Ralph is a Columbia CS student. His current courses include Operating Systems, Computer Networks, Algorithms, and Financial Accounting.\n"
                f"Today is {today}.{upcoming_section}\n\n"
                f"CRITICAL LANGUAGE RULES — follow these EXACTLY based on source label:\n\n"
                f"- If a source is labeled 'WROTE THIS' or 'BUILT THIS' or 'EDITED IN NOTION' or 'ATTENDED THIS MEETING' — "
                f"reference it as something he knows well: 'In your notes on X...', 'you wrote that...', 'in your meeting with...'\n"
                f"- If a source is labeled 'COURSE MATERIAL' — NEVER assume he absorbed it deeply. Teach it: "
                f"'Your [course name] material explains X as...' or 'There's a reading in your OS course that covers Y...' "
                f"Then offer: 'Want me to go deeper on this?'\n"
                f"- If a source is labeled 'SAVED' or is marked UNREAD/SAVED — "
                f"NEVER say 'you know' or 'you read'. Say: 'You saved a [article/video/paper] called [title] that covers X...' "
                f"Offer: 'Want me to walk you through the key ideas?'\n"
                f"- If a source is labeled 'STUDIED PREVIOUSLY' or 'OLDER MATERIAL' — "
                f"assume partial recall. Say 'you studied this before — it covered...', 'you may remember from your notes...'\n\n"
                f"NEVER say 'based on your knowledge base' or 'in your second brain' — be specific about the actual source.\n"
                f"NEVER say 'As an AI...' or mention being an AI.\n\n"
                f"CITATIONS: Use [1], [2], [3] markers inline when referencing a specific source so the UI can hyperlink them.\n\n"
                f"RESPONSE FORMAT:\n"
                f"- Lead with the direct answer in 1-2 sentences — no preamble.\n"
                f"- Use **bold** for key terms and concepts.\n"
                f"- Prefer 3-4 tight paragraphs over 10 bullet points. Only use bullets for genuinely list-like content.\n"
                f"- Use code blocks (```language) for any code, pseudocode, or command syntax.\n"
                f"- Cite sources inline with [N] markers when referencing specific content.\n"
                f"- Name specific people, projects, dates, and decisions from the sources.\n"
                f"- For exam-related questions: add a 'Key takeaway:' line at the end.\n"
                f"- Sources marked UNREAD/SAVED: say 'you saved' not 'you read'.\n"
                f"- Do not pad the answer — stop when the sources run out of relevant information.\n"
                f"- NEVER infer habits, routines, or frequency from individual data points.\n"
                f"- For OS/Networks/Algorithms/Accounting questions: be precise and technical.\n"
                f"- End your response with exactly this line:\n"
                f"  Sources: {source_names}\n\n"
                f"SOURCES:\n{context}\n\n"
                f"QUESTION: {req.q}"
            )

            anthropic_key = _os.environ.get("ANTHROPIC_API_KEY", "").strip()

            def _generate_related_questions(question: str, answer_snippet: str) -> list[str]:
                """Generate 3-4 related follow-up questions based on the question and answer."""
                try:
                    rq_prompt = (
                        f"Based on this question and answer, generate exactly 3 short, specific follow-up questions "
                        f"that a curious person would naturally want to ask next. "
                        f"Each question should be under 12 words, concrete, and directly related to the content.\n\n"
                        f"Question: {question}\n"
                        f"Answer snippet: {answer_snippet[:500]}\n\n"
                        f"Return ONLY a JSON array of 3 strings, no markdown, no extra text:\n"
                        f'["Question 1?", "Question 2?", "Question 3?"]'
                    )
                    raw = engine._chat(rq_prompt, max_tokens=200, model="claude-haiku-4-5-20251001")
                    import re as _rq_re
                    m = _rq_re.search(r'\[[\s\S]*?\]', raw)
                    if m:
                        parsed = _json.loads(m.group(0))
                        if isinstance(parsed, list):
                            return [str(q) for q in parsed[:4] if q]
                except Exception:
                    pass
                return []

            if anthropic_key:
                import anthropic
                client = anthropic.Anthropic(api_key=anthropic_key)
                full_text = ""
                with client.messages.stream(
                    model="claude-sonnet-4-6",
                    max_tokens=4000,
                    messages=[{"role": "user", "content": prompt}],
                ) as stream:
                    for text in stream.text_stream:
                        full_text += text
                        yield f"data: {_json.dumps({'type': 'token', 'text': text})}\n\n"
                related_questions = _generate_related_questions(req.q, full_text)
                yield f"data: {_json.dumps({'type': 'done', 'answer': full_text, 'sources': sources, 'related_questions': related_questions})}\n\n"
            else:
                # Non-streaming fallback
                answer = engine._chat(prompt, max_tokens=4000)
                related_questions = _generate_related_questions(req.q, answer)
                yield f"data: {_json.dumps({'type': 'done', 'answer': answer, 'sources': sources, 'related_questions': related_questions})}\n\n"

        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'detail': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/ask/quick", dependencies=_auth)
def ask_quick(req: QueryRequest):
    """Fast ask: top-5 chunks + brief prompt. Plain JSON, no SSE. For mobile / quick queries."""
    import os as _os
    engine = get_engine()
    store = get_store()
    if store.count() == 0:
        raise HTTPException(status_code=400, detail="Knowledge base is empty.")
    try:
        from ..retrieval.engine import _build_numbered_context
        results = store.search(req.q, n_results=5)
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        if not docs:
            return {"answer": "Nothing relevant found.", "sources": [], "question": req.q}
        context, sources = _build_numbered_context(docs, metas)
        prompt = (
            f"Answer this question briefly and directly using ONLY the sources below. "
            f"Cite sources inline like [1]. If sources don't answer it, say so.\n\n"
            f"SOURCES:\n{context}\n\nQUESTION: {req.q}"
        )
        answer = engine._chat(prompt, max_tokens=600, model="claude-haiku-4-5-20251001")
        return {"answer": answer, "sources": sources, "question": req.q}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"LLM unavailable: {e}")


@app.get("/ask/explain", dependencies=_auth)
async def ask_explain(term: str, request: Request):
    """Quick 2-3 sentence explanation of a term from the knowledge base."""
    import os as _os
    # Check cache first
    cache_key = f"explain:{term.lower().strip()}"
    cached = _mc_get(cache_key)
    if cached:
        return cached

    engine = get_engine()
    store = get_store()
    if store.count() == 0:
        raise HTTPException(status_code=400, detail="Knowledge base is empty.")

    # Retrieve relevant context via hybrid search
    loop = asyncio.get_event_loop()
    scored = await loop.run_in_executor(
        None, lambda: engine._hybrid_search(term, n_candidates=50)
    )
    context = "\n".join(
        item[1][:300] for item in scored[:3]
    )

    prompt = f"Based on these notes, explain '{term}' in 2-3 clear sentences:\n\n{context}"
    try:
        answer = await loop.run_in_executor(
            None,
            lambda: engine._chat(prompt, max_tokens=200, model="claude-haiku-4-5-20251001"),
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"LLM unavailable: {e}")

    sources = [
        {
            "content": item[1][:300],
            "title": item[2].get("title", ""),
            "source": item[2].get("source", ""),
        }
        for item in scored[:3]
    ]
    result = {"term": term, "explanation": answer, "sources": sources}
    _mc_set(cache_key, result, ttl_seconds=3600)
    return result


@app.post("/context", dependencies=_auth)
def context_pack(req: QueryRequest):
    try:
        return get_engine().context_pack(req.q, n_results=req.n_results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/resurface", dependencies=_auth)
def resurface(req: QueryRequest):
    try:
        return get_engine().resurface(req.q, n_results=req.n_results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/resurface/random", dependencies=_auth)
def resurface_random():
    """Surface a random piece of knowledge from the past — 'On This Day' style.

    Picks a random time window and seed query, returns 1-3 interesting chunks
    the user probably hasn't thought about recently, with an AI 'remember this?' prompt.
    """
    import random as _random
    from datetime import date as _date, timedelta as _td
    from ..retrieval.engine import _build_numbered_context, _extract_date

    engine = get_engine()
    store = get_store()

    if store.count() == 0:
        raise HTTPException(status_code=400, detail="Knowledge base is empty.")

    # Randomly pick a time window: somewhere between 3 months and 3 years ago
    days_back = _random.randint(90, 1095)
    window_start = (_date.today() - _td(days=days_back + 30)).isoformat()
    window_end = (_date.today() - _td(days=days_back)).isoformat()

    # Random seed query from a diverse set of intellectual domains
    seed_queries = [
        "insight idea theory observation",
        "book highlight quote lesson",
        "meeting decision project discussion",
        "concept explained example definition",
        "note reflection thought question",
        "argument claim evidence reasoning",
        "history philosophy ethics paradox",
        "technology code architecture design",
        "mathematics proof algorithm problem",
        "science research finding conclusion",
    ]
    query = _random.choice(seed_queries)

    # Search with high shuffle factor so different items surface each call
    scored = engine._hybrid_search(query, n_candidates=500, shuffle_factor=0.4)

    # High-value sources: filter to meaningful knowledge (avoid calendar spam, low-value files)
    HIGH_VALUE_SOURCES = {"apple_notes", "granola", "canvas", "notion", "kindle", "readwise", "note"}
    LOW_VALUE_SOURCES = {"calendar", "google_calendar", "gmail", "folder"}
    MIN_CHUNK_LENGTH = 150

    def _is_high_value(doc: str, meta: dict) -> bool:
        src = meta.get("source", "")
        # Always exclude low-value sources
        if src in LOW_VALUE_SOURCES:
            return False
        # Exclude spotify (music liked lists) — not meaningful knowledge
        if src == "spotify":
            return False
        # Enforce minimum chunk length for substance
        body = doc
        if "\n\n" in doc:
            body = doc.split("\n\n", 1)[1]
        if len(body.strip()) < MIN_CHUNK_LENGTH:
            return False
        if src == "goodreads":
            # Only include goodreads items rated >= 4
            rating = meta.get("rating", 0)
            try:
                return float(rating) >= 4
            except (TypeError, ValueError):
                return False
        # For files: skip if title looks like just a filename with no content
        if src == "file":
            title = meta.get("title", "")
            if title.endswith((".pdf", ".docx", ".pptx", ".xlsx", ".txt")) and len(title) < 30:
                return False
        return src in HIGH_VALUE_SOURCES or src not in LOW_VALUE_SOURCES

    high_value_scored = [x for x in scored if _is_high_value(x[1], x[2])]
    # Use high-value filtered set; fall back to all if too few results
    search_pool = high_value_scored if len(high_value_scored) >= 10 else scored

    # Filter to the random time window; fall back to any old content if window is empty
    windowed = [
        x for x in search_pool
        if _extract_date(x[2]) and window_start <= _extract_date(x[2]) <= window_end
    ]
    if not windowed:
        three_months_ago = (_date.today() - _td(days=90)).isoformat()
        windowed = [
            x for x in search_pool
            if _extract_date(x[2]) and _extract_date(x[2]) <= three_months_ago
        ]
    if not windowed:
        windowed = search_pool  # last resort: high-value anything

    # Pick 1-3 items at random from the top-50 of the filtered set
    pool = windowed[:50]
    n_pick = min(_random.randint(1, 3), len(pool))
    picks = _random.sample(pool, n_pick)
    picks.sort(key=lambda x: x[0], reverse=True)

    docs = [x[1] for x in picks]
    metas = [x[2] for x in picks]
    context, sources = _build_numbered_context(docs, metas)

    approx_period = (
        f"roughly {days_back} days ago"
        if days_back < 365
        else f"roughly {days_back // 365} year(s) ago"
    )
    result = engine._chat(
        f"You are Neuron — Ralph's second brain.\n\n"
        f"The following content is from his knowledge base from {approx_period}. "
        f"He probably hasn't thought about it recently. Write a brief, warm 'hey, remember this?' message "
        f"(2-4 sentences) that:\n"
        f"- Names what it is specifically (source, title, date)\n"
        f"- Highlights the single most interesting or useful idea from it\n"
        f"- Connects it to something he might be thinking about now\n"
        f"- Ends with one question that invites him to reflect or go deeper\n\n"
        f"Tone: like a thoughtful friend who just found an old note of yours. Second person. No preamble.\n\n"
        f"CONTENT:\n{context}",
        max_tokens=400,
    )

    # Build enriched source metadata
    enriched_sources = []
    for pick_score, pick_doc, pick_meta, pick_id in picks:
        enriched_sources.append({
            "title": pick_meta.get("title", ""),
            "source": pick_meta.get("source", ""),
            "source_date": pick_meta.get("date") or pick_meta.get("created_at") or pick_meta.get("source_date") or "",
            "url": pick_meta.get("url") or pick_meta.get("link") or "",
            "full_content": pick_doc,
        })

    # Find a "connection" to recent knowledge (what did he save recently on a related topic?)
    connection_hint = ""
    try:
        recent_scored = engine._hybrid_search(query, n_candidates=100, shuffle_factor=0.0)
        thirty_days_ago = (_date.today() - _td(days=30)).isoformat()
        recent_items = [
            x for x in recent_scored
            if _extract_date(x[2]) and _extract_date(x[2]) >= thirty_days_ago
        ]
        if recent_items:
            rc = recent_items[0]
            connection_hint = rc[2].get("title", "") or rc[1][:80]
    except Exception:
        pass

    return {
        "result": result,
        "sources": sources,
        "enriched_sources": enriched_sources,
        "period": approx_period,
        "days_back": days_back,
        "connection": connection_hint,
    }


@app.get("/resurface/topic", dependencies=_auth)
def resurface_topic(topic: str, n: int = 3):
    """Surface memories related to a specific topic — like a targeted 'remember this?' for a subject.

    Query params:
      topic — the topic to search for (required)
      n     — number of items to surface (default 3)
    """
    import random as _random
    from datetime import date as _date, timedelta as _td
    from ..retrieval.engine import _build_numbered_context, _extract_date

    engine = get_engine()
    store = get_store()

    if store.count() == 0:
        raise HTTPException(status_code=400, detail="Knowledge base is empty.")
    if not topic.strip():
        raise HTTPException(status_code=400, detail="topic param is required.")

    # Use hybrid search to find the most relevant chunks for this topic
    scored = engine._hybrid_search(topic, n_candidates=300, shuffle_factor=0.15)

    HIGH_VALUE_SOURCES = {"apple_notes", "granola", "canvas", "notion", "kindle", "readwise", "note"}
    LOW_VALUE_SOURCES = {"calendar", "google_calendar", "gmail", "folder", "spotify"}
    MIN_CHUNK_LENGTH = 100

    filtered = [
        x for x in scored
        if x[2].get("source", "") not in LOW_VALUE_SOURCES
        and len(x[1].strip()) >= MIN_CHUNK_LENGTH
    ]
    if not filtered:
        filtered = scored

    pool = filtered[:50]
    n_pick = min(n, len(pool))
    picks = _random.sample(pool[:20], n_pick)
    picks.sort(key=lambda x: x[0], reverse=True)

    docs = [x[1] for x in picks]
    metas = [x[2] for x in picks]
    context, sources = _build_numbered_context(docs, metas)

    enriched_sources = []
    for pick_score, pick_doc, pick_meta, pick_id in picks:
        enriched_sources.append({
            "title": pick_meta.get("title", ""),
            "source": pick_meta.get("source", ""),
            "source_date": pick_meta.get("date") or pick_meta.get("created_at") or pick_meta.get("source_date") or "",
            "url": pick_meta.get("url") or pick_meta.get("link") or "",
            "full_content": pick_doc,
        })

    result = engine._chat(
        f"You are Neuron — Ralph's second brain.\n\n"
        f"He wants to revisit what he knows about: {topic}\n\n"
        f"The following content is from his knowledge base on this topic. "
        f"Write a warm, insightful 'here's what you knew about this' message (2-4 sentences) that:\n"
        f"- Highlights the most interesting or surprising thing in this content\n"
        f"- Notes where it came from (source/title)\n"
        f"- Connects it to why this topic matters or what he could do with it\n"
        f"- Ends with one question to deepen his thinking\n\n"
        f"Tone: enthusiastic curator. Second person. No preamble.\n\n"
        f"CONTENT:\n{context}",
        max_tokens=400,
    )

    return {
        "result": result,
        "topic": topic,
        "sources": sources,
        "enriched_sources": enriched_sources,
    }


@app.post("/connections", dependencies=_auth)
def connections(req: QueryRequest):
    try:
        return get_engine().connections(req.q, n_results=15)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/digest", dependencies=_auth)
async def digest(refresh: bool = False):
    """Daily digest — cached 12 hours. Pass ?refresh=true to regenerate."""
    import asyncio, json
    from pathlib import Path
    from datetime import datetime, timedelta

    cache_path = Path.home() / ".neuron" / "digest_cache.json"

    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(hours=12):
                return JSONResponse(content=cached, headers={"Cache-Control": "max-age=43200, private"})
        except Exception:
            pass

    try:
        loop = asyncio.get_event_loop()
        engine = get_engine()
        result = await loop.run_in_executor(None, engine.digest)
        result["cached_at"] = datetime.now().isoformat()
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return JSONResponse(content=result, headers={"Cache-Control": "max-age=43200, private"})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Digest failed: {e}")


@app.get("/digest/stream", dependencies=_auth)
def digest_stream(refresh: bool = False):
    """Streaming version of /digest — returns SSE with token-by-token generation."""
    import json as _json
    import os as _os
    from datetime import datetime, timedelta

    cache_path = Path.home() / ".neuron" / "digest_cache.json"

    # Serve from cache if fresh and not forcing refresh
    if not refresh and cache_path.exists():
        try:
            cached = _json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(hours=12):
                def _cached_gen():
                    result_text = cached.get("result", "")
                    yield f"data: {_json.dumps({'type': 'done', 'result': result_text, 'sources': cached.get('sources', [])})}\n\n"
                return StreamingResponse(_cached_gen(), media_type="text/event-stream")
        except Exception:
            pass

    def generate():
        try:
            engine = get_engine()
            store = get_store()
            if store.count() == 0:
                yield f"data: {_json.dumps({'type': 'done', 'result': 'Knowledge base is empty.', 'sources': []})}\n\n"
                return

            # Build digest context (reuse engine internals)
            from ..retrieval.engine import _build_numbered_context
            from datetime import datetime as _dt
            _now = _dt.now()
            today = _now.strftime("%A, %B ") + str(_now.day) + _now.strftime(", %Y")

            seed_queries = [
                "idea concept theory framework insight argument",
                "book reading highlight chapter lesson learned",
                "article essay thesis claim evidence",
                "research paper finding result conclusion",
                "podcast lecture talk explanation",
                "class lecture course notes concepts",
                "definition explained example counterexample",
                "problem question hypothesis wondering",
                "connects relates similar parallel pattern",
                "contrast difference tension paradox",
                "implication consequence therefore means",
                "history philosophy ethics politics economics",
                "technology science mathematics physics",
                "literature writing language culture art",
                "religion theology ethics tradition",
                "business strategy product market",
                "artificial intelligence machine learning",
            ]
            best: dict = {}
            _DIGEST_EXCLUDE_SOURCES = {"calendar", "gmail", "google_calendar", "apple_calendar"}
            import re as _re_digest_stream
            _DIGEST_EXCLUDE_TITLE_PATTERNS = [
                r'\b(exam|midterm|final|quiz|office hours|lecture)\b.*\d{1,2}:\d{2}',
                r'\d{1,2}:\d{2}\s*(am|pm)',
                r'\b(due|deadline)\b.*\d{1,2}/\d{1,2}',
            ]

            def _stream_digest_should_exclude(title: str, source: str) -> bool:
                if source.lower() in _DIGEST_EXCLUDE_SOURCES:
                    return True
                for pat in _DIGEST_EXCLUDE_TITLE_PATTERNS:
                    if _re_digest_stream.search(pat, title, _re_digest_stream.IGNORECASE):
                        return True
                return False

            for query in seed_queries:
                for score, doc, meta, doc_id in engine._hybrid_search(query, n_candidates=80):
                    # Pre-filter excluded sources before they enter the pool
                    if _stream_digest_should_exclude(
                        meta.get("title", ""),
                        meta.get("source", "").lower().strip(),
                    ):
                        continue
                    if doc_id not in best or score > best[doc_id][0]:
                        best[doc_id] = (score, doc, meta, doc_id)

            sorted_items = sorted(best.values(), key=lambda x: x[0], reverse=True)[:60]
            all_docs  = [x[1] for x in sorted_items]
            all_metas = [x[2] for x in sorted_items]
            context, sources = _build_numbered_context(all_docs, all_metas)

            # Inject SRS due topics if any
            from datetime import date as _date_digest
            srs_data = _load_srs_data()
            today_iso = _date_digest.today().isoformat()
            srs_due_topics = [
                t.get("display_name", k)
                for k, t in srs_data.get("topics", {}).items()
                if t.get("next_review") and t["next_review"] <= today_iso
            ]
            srs_section = ""
            if srs_due_topics:
                topics_str = ", ".join(srs_due_topics[:5])
                srs_section = (
                    f"\n\n## Review Queue\n"
                    f"These topics are due for spaced repetition review today: {topics_str}. "
                    f"Name ONE of them explicitly and say: 'Your spaced review for [topic] is due today.' "
                    f"Then give one specific thing to focus on when reviewing it, grounded in the sources.\n"
                )

            prompt = (
                f"You are Neuron — Ralph's second brain and learning partner. Today is {today}.\n"
                f"Ralph is a Columbia CS student. His intellectual world spans: OS/Networks/Algorithms coursework, Torah & Jewish thought, Israel/geopolitics, AI & startups, finance, and personal projects.\n\n"
                f"Below are excerpts from his knowledge base — things he has read, highlighted, saved, and studied.\n\n"
                f"Write a morning briefing that feels like a message from a brilliant friend who has read everything in his library and noticed something he hasn't. "
                f"Open with the single most interesting, non-obvious thing — not a summary, but an insight. Never start with a greeting.\n\n"
                f"## What You're In Right Now\n"
                f"2–3 sentences on the ideas Ralph is most actively wrestling with. "
                f"Name actual titles, courses, or concepts from the sources — never vague categories. Specific, not generic.\n\n"
                f"## Ideas Worth Sitting With\n"
                f"2–3 specific arguments, questions, or passages from the sources that deserve attention today. "
                f"Quote or closely paraphrase the actual text. Make each one feel like it was written for him right now.\n\n"
                f"## A Connection You Might Have Missed\n"
                f"One non-obvious link between two things in his library from DIFFERENT domains. "
                f"Format: 'Your [source A] and [source B] are both making the same argument about X — specifically because...'\n\n"
                f"## One Thread to Pull Today\n"
                f"Name exactly ONE specific concept, question, or thinker worth going deeper on today. Make it feel timely.\n\n"
                + srs_section +
                f"\nABSOLUTE RULES — NEVER VIOLATE:\n"
                f"- NEVER mention calendar events, exam dates, assignment deadlines, or scheduled meetings by name\n"
                f"- NEVER use bullet points with '•' — use markdown '- ' instead\n"
                f"- NEVER include raw URLs in the output\n"
                f"- Keep each section to 2-3 sentences maximum\n"
                f"- NO email subject lines or email content of any kind\n"
                f"- NO emojis whatsoever\n"
                f"- NO phrases like 'Based on your knowledge base' or 'According to your notes'\n"
                f"- Write in clean prose paragraphs\n"
                f"- Sound like a thoughtful, brilliant friend — not an AI assistant\n"
                f"- Under 450 words total\n"
                f"- Open with the most important or surprising item — never with a greeting or preamble\n"
                f"- NO inline citations like [1] — reference sources by name naturally in prose\n"
                f"- Grounded entirely in the sources below — do not invent\n\n"
                f"KNOWLEDGE SOURCES:\n{context}"
            )

            anthropic_key = _os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not anthropic_key:
                # Fallback: non-streaming
                result_text = engine._chat(prompt, max_tokens=1500)
                yield f"data: {_json.dumps({'type': 'done', 'result': result_text, 'sources': sources})}\n\n"
                return

            # Send sources immediately
            yield f"data: {_json.dumps({'type': 'sources', 'sources': sources})}\n\n"

            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            full_text = ""
            with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for text in stream.text_stream:
                    full_text += text
                    yield f"data: {_json.dumps({'type': 'token', 'text': text})}\n\n"

            # Post-processing cleanup on the completed digest text
            import re as _re_digest
            full_text = full_text.strip()
            full_text = _re_digest.sub(r'\r\n', '\n', full_text)
            _digest_lines = full_text.split('\n')
            _digest_lines = [l for l in _digest_lines if not _re_digest.search(
                r'\b(exam|quiz|deadline|assignment|due|meeting|lecture|class|office hours)\b',
                l, _re_digest.IGNORECASE
            )]
            full_text = _re_digest.sub(r'\n{3,}', '\n\n', '\n'.join(_digest_lines))
            full_text = '\n'.join(l.rstrip() for l in full_text.split('\n')).strip()

            # Cache the result
            cache_result = {"result": full_text, "sources": sources, "topic": "digest",
                            "cached_at": datetime.now().isoformat()}
            cache_path.parent.mkdir(exist_ok=True)
            try:
                cache_path.write_text(_json.dumps(cache_result))
            except Exception:
                pass

            yield f"data: {_json.dumps({'type': 'done', 'result': full_text, 'sources': sources})}\n\n"

        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'detail': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")




class CanvasConfigRequest(BaseModel):
    token: str
    base_url: str = ""

class ReadwiseConfigRequest(BaseModel):
    token: str

@app.post("/config/canvas", dependencies=_auth)
def config_canvas(req: CanvasConfigRequest):
    """Save Canvas API token to .env (called from onboarding wizard)."""
    import re
    from pathlib import Path as _Path
    env_path = _Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        text = env_path.read_text()
        text = re.sub(r'^CANVAS_API_TOKEN=.*$', f'CANVAS_API_TOKEN={req.token.strip()}', text, flags=re.MULTILINE)
        if req.base_url:
            text = re.sub(r'^CANVAS_API_URL=.*$', f'CANVAS_API_URL={req.base_url.strip()}', text, flags=re.MULTILINE)
        env_path.write_text(text)
    import os as _os
    _os.environ["CANVAS_API_TOKEN"] = req.token.strip()
    if req.base_url:
        _os.environ["CANVAS_API_URL"] = req.base_url.strip()
    return {"ok": True}

@app.post("/config/readwise", dependencies=_auth)
def config_readwise(req: ReadwiseConfigRequest):
    """Save Readwise token to .env."""
    import re
    from pathlib import Path as _Path
    env_path = _Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        text = env_path.read_text()
        if "READWISE_API_TOKEN=" in text:
            text = re.sub(r'^READWISE_API_TOKEN=.*$', f'READWISE_API_TOKEN={req.token.strip()}', text, flags=re.MULTILINE)
        else:
            text += f"\nREADWISE_API_TOKEN={req.token.strip()}\n"
        env_path.write_text(text)
    import os as _os
    _os.environ["READWISE_API_TOKEN"] = req.token.strip()
    return {"ok": True}

@app.get("/auth/google", dependencies=_auth)
def auth_google():
    """Return Google OAuth URL for onboarding."""
    try:
        from ..ingestion.google_auth import get_auth_url
        return {"auth_url": get_auth_url()}
    except Exception as e:
        return {"auth_url": None, "message": str(e)}


@app.post("/refresh", dependencies=_auth)
def refresh():
    """Re-run all live ingesters and bust all AI caches."""
    import os as _os
    from pathlib import Path as _Path
    from ..config import (
        CANVAS_API_TOKEN, CANVAS_API_URL,
        NOTION_API_TOKEN, READWISE_API_TOKEN,
        POCKET_CONSUMER_KEY, POCKET_ACCESS_TOKEN,
        TRAKT_CLIENT_ID, TRAKT_USERNAME,
        SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET,
        GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
        WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET,
    )
    # Bust all AI-generated caches so they regenerate with fresh data
    for fname in ("digest_cache.json", "daily_cache.json", "news_cache.json", "news_summary_cache.json",
                  "recs_cache.json", "sparks_cache.json", "suggestions_cache.json", "learning_report_cache.json",
                  "analogies_cache.json"):
        try:
            (_Path.home() / ".neuron" / fname).unlink(missing_ok=True)
        except Exception:
            pass
    # Also bust in-memory caches
    _mem_cache.clear()
    store = get_store()
    results = {}

    def _run_ingester(label, fn):
        try:
            docs = fn()
            chunks, n = _chunk_and_store(docs, store)
            results[label] = {"ok": True, "chunks": chunks, "documents": n}
        except Exception as e:
            results[label] = {"ok": False, "error": str(e)}

    if CANVAS_API_TOKEN:
        from ..ingestion.canvas import CanvasIngester
        _run_ingester("canvas", lambda: CanvasIngester(CANVAS_API_TOKEN, CANVAS_API_URL).ingest())

    if NOTION_API_TOKEN:
        from ..ingestion.notion import NotionIngester
        _run_ingester("notion", lambda: NotionIngester(NOTION_API_TOKEN).ingest())

    if READWISE_API_TOKEN:
        from ..ingestion.readwise import ReadwiseIngester
        _run_ingester("readwise", lambda: ReadwiseIngester(READWISE_API_TOKEN).ingest())

    if POCKET_CONSUMER_KEY and POCKET_ACCESS_TOKEN:
        from ..ingestion.pocket import PocketIngester
        _run_ingester("pocket", lambda: PocketIngester(POCKET_CONSUMER_KEY, POCKET_ACCESS_TOKEN).ingest())

    if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
        from ..ingestion.spotify import SpotifyIngester
        _run_ingester("spotify", lambda: SpotifyIngester(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET).ingest())

    if WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET:
        from ..ingestion.whoop import WhoopIngester
        _run_ingester("whoop", lambda: WhoopIngester(WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET).ingest(days=30))

    if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
        from ..ingestion.google_auth import get_all_credentials
        from ..ingestion.google_calendar import GoogleCalendarIngester
        from ..ingestion.gmail import GmailIngester
        from ..ingestion.google_drive import GoogleDriveIngester
        accounts = get_all_credentials(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
        for label, creds in accounts:
            _run_ingester(f"gcal_{label}", lambda c=creds, l=label: GoogleCalendarIngester(c, l).ingest())
            _run_ingester(f"gmail_{label}", lambda c=creds, l=label: GmailIngester(c, l).ingest(days=30))

    # Invalidate caches after refresh so next load picks up new content
    eng = get_engine()
    eng._upcoming_cache.clear()
    from pathlib import Path as _Path
    for _cache in ("sparks_cache.json", "suggestions_cache.json", "timeline_cache.json"):
        try:
            (_Path.home() / ".neuron" / _cache).unlink(missing_ok=True)
        except Exception:
            pass

    return {"ok": True, "results": results}


@app.get("/upcoming", dependencies=_auth)
async def upcoming(days: int = 14):
    """What's on your calendar in the next N days?"""
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        engine = get_engine()
        return await loop.run_in_executor(None, lambda: engine.upcoming(days=days))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/today", dependencies=_auth)
async def today_combined():
    """Single endpoint returning all Home page data in parallel -- one call to rule them all.

    Returns a flat structure with everything the iOS HomeView and web home panel need:
    fact, vocab, events, digest, srs_due, spark, suggestions, analogy, resurface.
    All sub-calls use their individual caches so this is fast on repeat loads.
    Cached 15 minutes in memory.
    """
    import asyncio, json as _json
    from pathlib import Path as _Path
    from datetime import datetime as _dt, timedelta as _td, date as _date

    # Fast in-memory cache for the full /today response
    _today_mem = _mc_get("today")
    if _today_mem and _today_mem.get("date") == _date.today().isoformat():
        return _today_mem

    loop = asyncio.get_event_loop()
    engine = get_engine()

    def _read_cache(fname: str, max_hours: float):
        p = _Path.home() / ".neuron" / fname
        if not p.exists():
            return None
        try:
            d = _json.loads(p.read_text())
            ca = d.get("cached_at")
            if ca and _dt.now() - _dt.fromisoformat(ca) < _td(hours=max_hours):
                return d
        except Exception:
            pass
        return None

    def _read_daily_cache():
        p = _Path.home() / ".neuron" / "daily_cache.json"
        if not p.exists():
            return None
        try:
            d = _json.loads(p.read_text())
            if d.get("date") == _date.today().isoformat():
                return d
        except Exception:
            pass
        return None

    def _srs_due_inline() -> list:
        from datetime import date as _d
        today_iso = _d.today().isoformat()
        try:
            data = _load_srs_data()
            due = []
            for key, t in data.get("topics", {}).items():
                nr = t.get("next_review")
                if nr and nr <= today_iso:
                    due.append({
                        "topic": t.get("display_name", key),
                        "next_review": nr,
                        "repetitions": t.get("repetitions", 0),
                        "ef": t.get("ef", 2.5),
                        "overdue_days": (_d.fromisoformat(today_iso) - _d.fromisoformat(nr)).days,
                        "last_reviewed": t.get("last_reviewed"),
                    })
            return sorted(due, key=lambda x: x["overdue_days"], reverse=True)
        except Exception:
            return []

    def _get_daily():
        cached = _read_daily_cache()
        return cached if cached else engine.daily()

    def _get_digest():
        cached = _read_cache("digest_cache.json", 12)
        return cached if cached else engine.digest()

    def _get_spark():
        cached = _read_cache("sparks_cache.json", 24)
        if cached:
            return cached
        try:
            return engine.spark(days_recent=14, days_old=60)
        except Exception:
            return {}

    def _get_suggestions():
        # Use cache only; expensive AI call — skip if not warm
        cached_mem = _mc_get("suggestions")
        if cached_mem:
            return cached_mem
        cached = _read_cache("suggestions_cache.json", 2)
        return cached if cached else {}

    def _get_analogy():
        cached_mem = _mc_get("analogies")
        if cached_mem:
            return cached_mem
        cached = _read_cache("analogies_cache.json", 6)
        return cached if cached else {}

    def _get_resurface():
        # Resurface is intentionally random and not cached — skip on /today for speed
        # (it appears when the individual endpoint warms it)
        try:
            import random as _random
            from datetime import date as _d2, timedelta as _td2
            from ..retrieval.engine import _extract_date
            store = engine.store if hasattr(engine, 'store') else None
            # Quick: just return None here to keep /today fast; resurface loads async elsewhere
            return None
        except Exception:
            return None

    results = await asyncio.gather(
        loop.run_in_executor(None, _get_daily),
        loop.run_in_executor(None, lambda: engine.upcoming(days=7)),
        loop.run_in_executor(None, _get_digest),
        loop.run_in_executor(None, _get_spark),
        loop.run_in_executor(None, _get_suggestions),
        loop.run_in_executor(None, _get_analogy),
        loop.run_in_executor(None, _get_resurface),
        return_exceptions=True,
    )
    daily_r, upcoming_r, digest_r, spark_r, suggestions_r, analogy_r, resurface_r = results

    def _safe(r):
        return None if isinstance(r, Exception) else r

    daily_r       = _safe(daily_r) or {}
    upcoming_r    = _safe(upcoming_r) or {}
    digest_r      = _safe(digest_r) or {}
    spark_r       = _safe(spark_r) or {}
    suggestions_r = _safe(suggestions_r) or {}
    analogy_r     = _safe(analogy_r) or {}
    resurface_r   = _safe(resurface_r) or {}

    sparks_list    = spark_r.get("sparks", []) if isinstance(spark_r, dict) else []
    first_spark    = sparks_list[0] if sparks_list else None
    analogies_list = analogy_r.get("analogies", []) if isinstance(analogy_r, dict) else []
    first_analogy  = analogies_list[0] if analogies_list else None

    _today_result = {
        "fact":             daily_r.get("fact"),
        "vocab":            daily_r.get("vocab"),
        "date":             daily_r.get("date", _date.today().isoformat()),
        "events":           upcoming_r.get("events", []),
        "digest":           digest_r.get("result"),
        "digest_cached_at": digest_r.get("cached_at"),
        "srs_due":          _srs_due_inline(),
        "spark":            first_spark,
        "suggestions":      suggestions_r.get("suggestions", []),
        "analogy":          first_analogy,
        "resurface":        resurface_r if isinstance(resurface_r, dict) and resurface_r.get("result") else None,
    }
    _mc_set("today", _today_result, _TTL_TODAY)
    return _today_result


@app.get("/recent", dependencies=_auth)
def recent(days: int = 14):
    """What have you been taking in lately? Temporal browse by date."""
    try:
        return get_engine().recent(days=days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/graph-ui", response_class=HTMLResponse)
def graph_ui():
    return (UI_DIR / "graph.html").read_text()


@app.get("/graph", dependencies=_auth)
def graph_data():
    """Return cached topic graph, or signal that it needs to be built."""
    import json
    from pathlib import Path
    cache_path = Path.home() / ".neuron" / "graph_cache.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    return {"nodes": [], "edges": [], "needs_build": True}


@app.post("/graph/build", dependencies=_auth)
def graph_build():
    """Analyze KB with Claude and build topic graph. Takes ~15s."""
    try:
        return get_engine().build_topic_graph()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class NodeRequest(BaseModel):
    label: str
    category: str = ""


@app.post("/node/summary", dependencies=_auth)
def node_summary(req: NodeRequest):
    """On-demand AI summary for a clicked graph node."""
    try:
        import json as _json
        source_chunk_ids: list[str] = []
        cache_path = Path.home() / ".neuron" / "graph_cache.json"
        if cache_path.exists():
            cache = _json.loads(cache_path.read_text())
            for node in cache.get("nodes", []):
                if node.get("label") == req.label:
                    source_chunk_ids = node.get("source_chunk_ids", [])
                    break
        return get_engine().topic_summary(req.label, req.category, source_chunk_ids)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PracticeRequest(BaseModel):
    topic: str
    difficulty: str = "medium"  # "easy" | "medium" | "hard"


class EvaluateRequest(BaseModel):
    question: str
    user_answer: str
    correct_answer: str
    explanation: str
    topic: str


@app.post("/practice", dependencies=_auth)
def practice(req: PracticeRequest, request: Request):
    """Generate practice exercises on a topic from the user's knowledge base.
    Difficulty: easy | medium | hard | adaptive. Prioritizes course materials and injects exam context.
    Adaptive difficulty: checks SRS error rates and picks difficulty based on recent struggles."""
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip, "/practice", max_per_minute=5):
        raise HTTPException(status_code=429, detail="Rate limit exceeded: max 5 requests per minute for /practice.")
    try:
        difficulty = req.difficulty
        # Adaptive difficulty: check SRS error rates to pick the right level
        if difficulty == "adaptive":
            try:
                data = _load_srs_data()
                topics_srs = data.get("topics", {})
                topic_key = req.topic.lower().strip()
                related = {k: v for k, v in topics_srs.items() if topic_key in k or k in topic_key}
                if related:
                    recent_scores = []
                    for t_data in related.values():
                        for h in t_data.get("history", [])[-5:]:
                            recent_scores.append(1 if h.get("score") == "correct" else 0)
                    if recent_scores:
                        correct_rate = sum(recent_scores) / len(recent_scores)
                        if correct_rate < 0.4:
                            difficulty = "easy"
                        elif correct_rate > 0.7:
                            difficulty = "hard"
                        else:
                            difficulty = "medium"
                    else:
                        difficulty = "medium"
                else:
                    difficulty = "medium"
            except Exception:
                difficulty = "medium"
        return get_engine().practice(req.topic, difficulty=difficulty)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/practice/evaluate", dependencies=_auth)
def evaluate_answer(req: EvaluateRequest):
    """Evaluate a user's practice answer with AI feedback. Auto-seeds SRS card for the topic."""
    try:
        result = get_engine().evaluate_answer(
            req.question, req.user_answer, req.correct_answer, req.explanation, req.topic
        )
        # Auto-seed SRS: create or update an SRS entry for this topic based on the score
        try:
            from datetime import date, timedelta
            score = result.get("score", "partial")
            # SM-2 quality scale: correct=5 (perfect), partial=4 (hesitation), incorrect=1 (failed)
            score_map = {"correct": 5, "partial": 4, "incorrect": 1}
            score_num = score_map.get(score, 4)

            data = _load_srs_data()
            topics = data.setdefault("topics", {})
            topic_key = req.topic.lower().strip()

            if topic_key not in topics:
                topics[topic_key] = {
                    "ef": 2.5, "interval": 1, "repetitions": 0,
                    "last_reviewed": None, "next_review": None,
                    "history": [], "display_name": req.topic,
                }

            t = topics[topic_key]
            new_ef, new_interval, new_reps = _sm2_update(
                t.get("ef", 2.5), t.get("interval", 1), t.get("repetitions", 0), score_num
            )
            today_iso = date.today().isoformat()
            next_review = (date.today() + timedelta(days=new_interval)).isoformat()

            t["ef"] = new_ef
            t["interval"] = new_interval
            t["repetitions"] = new_reps
            t["last_reviewed"] = today_iso
            t["next_review"] = next_review
            t["display_name"] = req.topic
            t.setdefault("history", []).append({
                "date": today_iso, "score": score,
                "correct_count": 1 if score == "correct" else 0, "total_count": 1,
            })
            t["history"] = t["history"][-50:]

            # For wrong/partial answers: also create a per-question flashcard so
            # the specific question resurfaces via SRS
            if score_num <= 4:  # incorrect or partial
                cards = data.setdefault("cards", [])
                # Deduplicate: don't add same question twice
                q_trimmed = req.question[:200]
                existing = next((c for c in cards if c.get("question", "")[:200] == q_trimmed), None)
                if existing is None:
                    cards.append({
                        "question": req.question,
                        "answer": req.correct_answer,
                        "explanation": req.explanation,
                        "topic": req.topic,
                        "ef": 2.5,
                        "interval": 1,
                        "repetitions": 0,
                        "next_review": (date.today() + timedelta(days=1)).isoformat(),
                        "last_reviewed": today_iso,
                        "created": today_iso,
                        "score_on_create": score,
                    })
                    # Keep cards list bounded
                    data["cards"] = cards[-500:]
                    result["srs_card_created"] = True

            _save_srs_data(data)
            result["srs_updated"] = True
            result["next_review"] = next_review
        except Exception:
            pass  # SRS update failure is non-fatal

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/spark", dependencies=_auth)
async def spark(days_recent: int = 14, days_old: int = 60, refresh: bool = False):
    """Find unexpected connections between recent and older knowledge. Cached 24h (refreshes daily)."""
    import asyncio, json
    from pathlib import Path
    from datetime import datetime, timedelta, date

    cache_path = Path.home() / ".neuron" / "sparks_cache.json"
    mem_key = f"spark_{days_recent}_{days_old}"
    today_iso = date.today().isoformat()

    if not refresh:
        # 1. In-memory cache
        cached_mem = _mc_get(mem_key)
        if cached_mem and cached_mem.get("cache_date") == today_iso:
            return JSONResponse(content=cached_mem, headers={"Cache-Control": "max-age=86400, private", "X-Cache": "MEM"})
        # 2. File cache
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                if cached.get("cache_date") == today_iso:
                    _mc_set(mem_key, cached, _TTL_SPARK)
                    return JSONResponse(content=cached, headers={"Cache-Control": "max-age=86400, private", "X-Cache": "FILE"})
            except Exception:
                pass

    if refresh:
        _mc_delete(mem_key)

    try:
        loop = asyncio.get_event_loop()
        engine = get_engine()
        t0 = time.perf_counter()
        result = await loop.run_in_executor(None, lambda: engine.spark(days_recent=days_recent, days_old=days_old))
        logger.info("[TIMING] /spark LLM call: %.1fs", time.perf_counter() - t0)
        result["cached_at"] = datetime.now().isoformat()
        result["cache_date"] = today_iso
        _mc_set(mem_key, result, _TTL_SPARK)
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return JSONResponse(content=result, headers={"Cache-Control": "max-age=86400, private"})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Spark failed: {e}")


@app.get("/analogies", dependencies=_auth)
async def analogies(refresh: bool = False, random_topic: bool = False):
    """Find analogical bridges between different knowledge domains (e.g. Macro ↔ CS). Cached 2h.
    Pass random_topic=true to seed from a randomly chosen recent note title."""
    import asyncio, json, re, random as _random_a
    from pathlib import Path
    from datetime import datetime, timedelta
    from ..retrieval.engine import _extract_date

    cache_path = Path.home() / ".neuron" / "analogies_cache.json"

    # If random_topic mode, pick a topic from recent content and force a refresh pass
    _injected_topic: str | None = None
    if random_topic:
        try:
            store = get_store()
            seeds = [
                "main topic subject area concept",
                "key idea theory principle",
                "problem method technique approach",
            ]
            candidates: list[str] = []
            seen_t: set[str] = set()
            cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            for seed in seeds:
                res = store.search(seed, n_results=10)
                for meta in res["metadatas"][0]:
                    src = meta.get("source", "")
                    if src in ("calendar", "gmail"):
                        continue
                    d_str = _extract_date(meta)
                    title = meta.get("title", "").strip()
                    tag = title.split(":")[0].strip() if title else ""
                    if tag and tag not in seen_t:
                        seen_t.add(tag)
                        candidates.append(tag)
            if candidates:
                _injected_topic = _random_a.choice(candidates[:20])
        except Exception:
            pass
        refresh = True  # random_topic always bypasses cache

    if not refresh:
        # 1. In-memory cache
        cached_mem = _mc_get("analogies")
        if cached_mem:
            return JSONResponse(content=cached_mem, headers={"Cache-Control": "max-age=7200, private", "X-Cache": "MEM"})
        # 2. File cache
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
                if datetime.now() - cached_at < timedelta(hours=2):
                    _mc_set("analogies", cached, _TTL_ANALOGIES)
                    return JSONResponse(content=cached, headers={"Cache-Control": "max-age=7200, private", "X-Cache": "FILE"})
            except Exception:
                pass

    if refresh:
        _mc_delete("analogies")

    try:
        engine = get_engine()
        store = get_store()

        # Discover domain clusters from ALL corners of the KB
        import random as _random
        DOMAIN_SEEDS = [
            ("Operating Systems", "process scheduling virtual memory page table TLB system calls"),
            ("Computer Networks", "TCP congestion control routing BGP DNS packet switching"),
            ("Algorithms", "dynamic programming greedy NP-completeness graph algorithms amortized"),
            ("Distributed Systems", "consensus Raft Paxos fault tolerance replication CAP theorem"),
            ("Financial Accounting", "balance sheet journal entries revenue recognition depreciation GAAP"),
            ("Macroeconomics", "monetary policy interest rates GDP inflation supply demand fiscal policy"),
            ("Torah / Jewish Studies", "parasha halacha Talmud Jewish law Torah learning Rabbi"),
            ("History / Politics", "history political theory government policy elections empires"),
            ("Philosophy", "epistemology ethics reasoning logic consciousness free will"),
            ("Business / Startups", "startup venture capital product market fit strategy"),
            ("Mathematics", "probability statistics linear algebra calculus proofs"),
            ("Biology / Science", "biology genetics evolution systems biology emergence"),
        ]

        domain_samples: dict[str, list[tuple]] = {}
        seen_titles: set[str] = set()

        for domain_name, seed in DOMAIN_SEEDS:
            try:
                res = store.search(seed, n_results=8)
                items = []
                for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
                    title = meta.get("title", "")
                    src = meta.get("source", "")
                    if src in ("calendar", "gmail") or title in seen_titles:
                        continue
                    seen_titles.add(title)
                    items.append((doc, meta, title))
                if len(items) >= 1:
                    domain_samples[domain_name] = items[:4]
            except Exception:
                continue

        # Need at least 2 domains to make analogies
        populated = {k: v for k, v in domain_samples.items() if v}
        if len(populated) < 2:
            return JSONResponse(
                content={"analogies": [], "message": "Add more content across different subjects to find cross-domain connections.", "cached_at": datetime.now().isoformat()},
                headers={"Cache-Control": "max-age=3600, private"},
            )

        domain_names_used = list(populated.keys())

        # If random_topic mode, inject the chosen topic as extra search context for one domain
        if _injected_topic:
            try:
                res_inj = store.search(_injected_topic, n_results=6)
                inj_items = []
                for doc_i, meta_i in zip(res_inj["documents"][0], res_inj["metadatas"][0]):
                    title_i = meta_i.get("title", "")
                    src_i = meta_i.get("source", "")
                    if src_i in ("calendar", "gmail") or title_i in seen_titles:
                        continue
                    seen_titles.add(title_i)
                    inj_items.append((doc_i, meta_i, title_i))
                if inj_items:
                    populated[f"[{_injected_topic}]"] = inj_items[:3]
                    domain_names_used = list(populated.keys())
            except Exception:
                pass

        # Force cross-domain pairs: split into two halves and pair across halves to maximize distance
        all_domains = list(populated.keys())
        _random.shuffle(all_domains)

        # If random_topic injected a domain, make sure it appears in at least one pair
        _pinned: str | None = next((d for d in all_domains if d.startswith("[")), None)
        half = max(1, len(all_domains) // 2)
        left = all_domains[:half]
        right = all_domains[half:] if len(all_domains) > half else all_domains[:half]
        right_shifted = right[1:] + right[:1] if len(right) > 1 else right
        cross_pairs: list[tuple[str, str]] = []
        if _pinned and _pinned in all_domains:
            others = [d for d in all_domains if d != _pinned]
            if others:
                cross_pairs.append((_pinned, _random.choice(others)))
        for i in range(min(4, len(left), len(right_shifted))):
            a, b = left[i % len(left)], right_shifted[i % len(right_shifted)]
            if a != b and (a, b) not in cross_pairs and (b, a) not in cross_pairs:
                cross_pairs.append((a, b))
        _attempts = 0
        while len(cross_pairs) < 4 and len(all_domains) >= 2 and _attempts < 20:
            a, b = _random.sample(all_domains, 2)
            if (a, b) not in cross_pairs and (b, a) not in cross_pairs:
                cross_pairs.append((a, b))
            _attempts += 1

        # Build context for each forced cross-domain pair
        pair_ctx_parts = []
        for domain_a, domain_b in cross_pairs[:4]:
            items_a = populated[domain_a]
            items_b = populated[domain_b]
            ex_a = f'"{items_a[0][2][:80]}: {items_a[0][0][:200]}"'
            ex_b = f'"{items_b[0][2][:80]}: {items_b[0][0][:200]}"'
            pair_ctx_parts.append(
                f"PAIR: [{domain_a}] vs [{domain_b}]\n"
                f"  Domain A excerpt: {ex_a}\n"
                f"  Domain B excerpt: {ex_b}"
            )
        ctx = "\n\n".join(pair_ctx_parts)
        domains_list = ", ".join(domain_names_used)

        _ctx_a = ctx
        _dl_a = domains_list
        _engine_a = engine
        _loop_a = asyncio.get_event_loop()
        _t0_analogies = time.perf_counter()
        raw = await _loop_a.run_in_executor(None, lambda: _engine_a._chat(
            "You are a master educator who sees deep connections across domains.\n\n"
            f"The student has studied: {_dl_a}.\n\n"
            "For each PAIR below, find a SURPRISING, NON-OBVIOUS analogy between Domain A and Domain B.\n\n"
            "The analogy should:\n"
            "- Be conceptually deep (not superficial) — same underlying mechanism, not just similar vocabulary\n"
            "- Have a clear mapping between specific components in each domain\n"
            "- Illuminate something new about BOTH domains simultaneously\n\n"
            'Use this format: "CONCEPT from [Domain A] is like CONCEPT from [Domain B] because '
            '[mechanism that maps between them]. This reveals [non-obvious insight]."\n\n'
            "RULES:\n"
            "- Each analogy MUST bridge exactly the 2 domains listed in its PAIR\n"
            "- The connection must be structural/mechanistic — not just thematic similarity\n"
            "- Name SPECIFIC concepts, not domain names\n"
            "- Return ONLY valid JSON array, no markdown:\n"
            '[{"domain_a":"...","concept_a":"...","domain_b":"...","concept_b":"...",'
            '"analogy":"CONCEPT from [Domain A] is like CONCEPT from [Domain B] because [mechanism]. This reveals [insight].",'
            '"deeper_insight":"one sentence on what this reveals that you would not see from inside either domain alone"}]\n\n'
            f"CROSS-DOMAIN PAIRS TO ANALYZE:\n{_ctx_a}",
            max_tokens=1600,
            model="claude-sonnet-4-6",
        ))

        logger.info("[TIMING] /analogies LLM call: %.1fs", time.perf_counter() - _t0_analogies)

        m = re.search(r'\[[\s\S]*?\]', raw)
        analogy_list = []
        if m:
            try:
                parsed = json.loads(m.group(0))
                analogy_list = [a for a in parsed if isinstance(a, dict) and "analogy" in a]
            except Exception:
                pass

        result = {
            "analogies": analogy_list,
            "domains_found": domain_names_used,
            "cached_at": datetime.now().isoformat(),
        }
        _mc_set("analogies", result, _TTL_ANALOGIES)
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return JSONResponse(content=result, headers={"Cache-Control": "max-age=7200, private"})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Analogies failed: {e}")


@app.get("/cross-domain", dependencies=_auth)
async def cross_domain(topic: str = "", refresh: bool = False):
    """Find cross-domain connections for a topic, or pick 2 random distant domains. Cached 2h."""
    import asyncio, json, re, random as _random
    from pathlib import Path
    from datetime import datetime, timedelta

    cache_key = f"cross_domain_{topic.lower().strip()[:40]}" if topic else "cross_domain_random"
    cache_path = Path.home() / ".neuron" / f"{cache_key}.json"

    if not refresh:
        # 1. In-memory cache
        cached_mem = _mc_get(cache_key)
        if cached_mem:
            return JSONResponse(content=cached_mem, headers={"Cache-Control": "max-age=7200, private", "X-Cache": "MEM"})
        # 2. File cache
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
                if datetime.now() - cached_at < timedelta(hours=2):
                    _mc_set(cache_key, cached, _TTL_CROSS)
                    return JSONResponse(content=cached, headers={"Cache-Control": "max-age=7200, private", "X-Cache": "FILE"})
            except Exception:
                pass

    if refresh:
        _mc_delete(cache_key)

    try:
        engine = get_engine()
        store = get_store()

        DOMAIN_SEEDS = [
            ("Operating Systems", "process scheduling virtual memory page table TLB system calls"),
            ("Computer Networks", "TCP congestion control routing BGP DNS packet switching"),
            ("Algorithms", "dynamic programming greedy NP-completeness graph algorithms amortized"),
            ("Distributed Systems", "consensus Raft Paxos fault tolerance replication CAP theorem"),
            ("Financial Accounting", "balance sheet journal entries revenue recognition depreciation GAAP"),
            ("Macroeconomics", "monetary policy interest rates GDP inflation supply demand fiscal policy"),
            ("Torah / Jewish Studies", "parasha halacha Talmud Jewish law Torah learning Rabbi"),
            ("History / Politics", "history political theory government policy elections empires"),
            ("Philosophy", "epistemology ethics reasoning logic consciousness free will"),
            ("Business / Startups", "startup venture capital product market fit strategy"),
            ("Mathematics", "probability statistics linear algebra calculus proofs"),
            ("Biology / Science", "biology genetics evolution systems biology emergence"),
        ]

        domain_samples: dict[str, list[tuple]] = {}
        seen_titles: set[str] = set()

        for domain_name, seed in DOMAIN_SEEDS:
            try:
                res = store.search(seed, n_results=6)
                items = []
                for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
                    t = meta.get("title", "")
                    src = meta.get("source", "")
                    if src in ("calendar", "gmail") or t in seen_titles:
                        continue
                    seen_titles.add(t)
                    items.append((doc, meta, t))
                if items:
                    domain_samples[domain_name] = items[:3]
            except Exception:
                continue

        populated = {k: v for k, v in domain_samples.items() if v}
        if len(populated) < 2:
            return JSONResponse(
                content={"connections": [], "message": "Not enough cross-domain content yet.", "cached_at": datetime.now().isoformat()},
                headers={"Cache-Control": "max-age=3600, private"},
            )

        all_domains = list(populated.keys())

        if topic:
            # Find which domain the topic best fits into
            topic_lower = topic.lower()
            TOPIC_DOMAIN_MAP = {
                "os": "Operating Systems", "operating systems": "Operating Systems",
                "networks": "Computer Networks", "networking": "Computer Networks",
                "tcp": "Computer Networks", "algorithms": "Algorithms",
                "distributed": "Distributed Systems", "raft": "Distributed Systems",
                "accounting": "Financial Accounting", "macro": "Macroeconomics",
                "economics": "Macroeconomics", "torah": "Torah / Jewish Studies",
                "history": "History / Politics", "philosophy": "Philosophy",
                "startup": "Business / Startups", "math": "Mathematics",
                "biology": "Biology / Science",
            }
            domain_a = next(
                (v for k, v in TOPIC_DOMAIN_MAP.items() if k in topic_lower),
                None
            )
            # If no match, search and pick best matching domain
            if not domain_a or domain_a not in populated:
                try:
                    res = store.search(topic, n_results=5)
                    best_doc = res["documents"][0][0] if res["documents"][0] else ""
                    domain_a = _random.choice(all_domains)
                except Exception:
                    domain_a = all_domains[0]
            # Pick a distant domain (different from domain_a)
            other_domains = [d for d in all_domains if d != domain_a]
            if not other_domains:
                other_domains = all_domains
            # Maximize distance: prefer domains that are conceptually far
            _random.shuffle(other_domains)
            domain_b = other_domains[0]
        else:
            # Pick 2 random distant domains
            _random.shuffle(all_domains)
            half = max(1, len(all_domains) // 2)
            domain_a = all_domains[0]
            domain_b = all_domains[half] if len(all_domains) > half else all_domains[-1]
            if domain_a == domain_b and len(all_domains) >= 2:
                domain_b = all_domains[1]

        items_a = populated.get(domain_a, [])
        items_b = populated.get(domain_b, [])
        if not items_a or not items_b:
            # Fall back to any two populated domains
            keys = list(populated.keys())
            domain_a, domain_b = keys[0], keys[1]
            items_a, items_b = populated[domain_a], populated[domain_b]

        ex_a = f"{items_a[0][2][:80]}: {items_a[0][0][:300]}"
        ex_b = f"{items_b[0][2][:80]}: {items_b[0][0][:300]}"
        topic_clause = f" specifically around the topic of '{topic}'" if topic else ""

        _da, _db, _ea, _eb, _tc, _ad, _eng_cd = domain_a, domain_b, ex_a, ex_b, topic_clause, all_domains, engine
        _loop_cd = asyncio.get_event_loop()
        _t0_cross = time.perf_counter()
        raw = await _loop_cd.run_in_executor(None, lambda: _eng_cd._chat(
            "You are a master educator who sees deep connections across domains.\n\n"
            f"The student has studied: {', '.join(_ad)}.\n\n"
            f"Find 3 SURPRISING, NON-OBVIOUS connections between [{_da}] and [{_db}]{_tc}.\n\n"
            "For each connection:\n"
            "- Identify the specific concept in each domain\n"
            "- Explain WHY does this connection matter for understanding BOTH? (elaborative interrogation)\n"
            "- Make it actionable: when you see X in domain A, look for Y in domain B\n"
            "- Find the underlying mechanism that appears in both\n\n"
            f"Domain A [{_da}] excerpt: \"{_ea}\"\n"
            f"Domain B [{_db}] excerpt: \"{_eb}\"\n\n"
            "Return ONLY valid JSON array (no markdown, no backticks, no explanation — just the JSON):\n"
            '[{"domain_a":"' + _da + '","concept_a":"...","domain_b":"' + _db + '","concept_b":"...",'
            '"analogy":"...","insight":"Why does this connection matter for understanding both?","actionable":"When you see X in [domain_a], look for Y in [domain_b]"}]',
            max_tokens=1400,
            model="claude-sonnet-4-6",
        ))

        logger.info("[TIMING] /cross-domain LLM call: %.1fs", time.perf_counter() - _t0_cross)

        # Try to parse JSON — handle LLM wrapping in ```json ... ``` blocks
        connections = []
        raw_clean = raw.strip()
        # Strip markdown code fences if present
        raw_clean = re.sub(r'^```(?:json)?\s*', '', raw_clean)
        raw_clean = re.sub(r'\s*```$', '', raw_clean)
        m = re.search(r'\[[\s\S]*?\]', raw_clean)
        if m:
            try:
                parsed = json.loads(m.group(0))
                connections = [c for c in parsed if isinstance(c, dict)]
            except Exception:
                pass

        # Fallback: if LLM produced no valid connections, use a hardcoded interesting pair
        if not connections:
            HARDCODED_PAIRS = [
                {
                    "domain_a": "Operating Systems",
                    "concept_a": "Process scheduling (preemption & priority inversion)",
                    "domain_b": "Financial Accounting",
                    "concept_b": "Capital budgeting under resource scarcity",
                    "analogy": "Priority inversion in OS scheduling — where a low-priority process holds a lock needed by a high-priority one — maps exactly to capital rationing: a low-ROI project hoarding resources that a high-ROI project urgently needs. Both systems can deadlock when resource holders are not forced to yield.",
                    "insight": "Both reveal that priority alone is not enough; you need a protocol (priority inheritance / forced preemption) to prevent the queue from inverting in unexpected ways.",
                    "actionable": "When you see priority inversion in an OS, ask: which projects in your capital stack are holding the mutex? When studying capital rationing, ask: does your allocation protocol have a priority inheritance equivalent?",
                },
                {
                    "domain_a": "Computer Networks",
                    "concept_a": "TCP congestion control (AIMD + slow start)",
                    "domain_b": "Macroeconomics",
                    "concept_b": "Market price discovery under uncertainty",
                    "analogy": "TCP AIMD is structurally identical to how traders probe liquidity: bid cautiously (additive), but cut on a loss signal sharply (multiplicative). Both protocols solve the same problem — probing an unknown capacity boundary without catastrophic overshoot.",
                    "insight": "Both TCP and markets are distributed systems that must infer a hidden global state (bandwidth / true price) from local signals (packet loss / fill rate). The asymmetry of increase vs. decrease is a mathematical necessity for stability.",
                    "actionable": "When you see TCP slow start, ask: what is the market equivalent connection establishment phase? When a market gaps down violently, ask: what feedback signal triggered a multiplicative decrease?",
                },
                {
                    "domain_a": "Algorithms",
                    "concept_a": "Dynamic programming (overlapping subproblems + optimal substructure)",
                    "domain_b": "Biology / Science",
                    "concept_b": "DNA sequence alignment (Smith-Waterman / Needleman-Wunsch)",
                    "analogy": "Sequence alignment IS dynamic programming: the edit-distance recurrence is the Bellman equation applied to biological strings. Evolution, like DP, only needs to remember the boundary of what has already been solved — the rest of history is discardable.",
                    "insight": "This is not a metaphor — it is the same algorithm. Every bioinformatics tool that aligns genomes runs a DP. Understanding why DP works (reusing solutions to subproblems) explains why evolution is parsimonious: it reuses successful subsequences rather than redesigning from scratch.",
                    "actionable": "When you study a DP recurrence, ask: what biological process does this edit cost model? When you study a mutation, ask: what DP gap penalty does natural selection impose?",
                },
                {
                    "domain_a": "Distributed Systems",
                    "concept_a": "Consensus under Byzantine faults (Raft / Paxos)",
                    "domain_b": "Torah / Jewish Studies",
                    "concept_b": "Halachic adjudication across a distributed rabbinic network",
                    "analogy": "Raft requires a quorum of honest nodes to commit a log entry; halachic consensus requires a majority of qualified poskim whose reasoning can be traced. Both tolerate faulty participants, require a term-limited leader, and handle forks through explicit conflict-resolution protocols.",
                    "insight": "The Talmud machlokot (disputes) are not bugs but features — minority opinions are preserved as log entries that may become the majority view under different future conditions, exactly as Paxos preserves historical ballot values.",
                    "actionable": "When you study Paxos Phase 1, ask: what is the halachic equivalent of locking a value before committing? When you encounter a minority Talmudic opinion, ask: under what network partition would this node have been the correct leader?",
                },
                {
                    "domain_a": "Philosophy",
                    "concept_a": "Epistemology (justified true belief vs. Gettier problems)",
                    "domain_b": "Macroeconomics",
                    "concept_b": "Rational expectations and signal extraction",
                    "analogy": "A Gettier case — where an agent holds a true, justified belief formed for the wrong reasons — is structurally identical to a rational-expectations equilibrium where the forecast is correct but derived from a misspecified model. Both are accidentally right, yet neither agent genuinely knows.",
                    "insight": "Economics Lucas critique and philosophy Gettier problem share a common core: a valid output produced by a faulty process is not knowledge. Both fields spent decades redesigning what it means to be right for the right reasons.",
                    "actionable": "When an economic model predicts correctly, ask: is this a Gettier case — right for the right reasons? When studying epistemology, ask: what is the rational-expectations equivalent of your justification condition?",
                },
            ]
            # Pick the pair most relevant to the chosen domains, or just use first
            best = HARDCODED_PAIRS[0]
            for pair in HARDCODED_PAIRS:
                if pair["domain_a"].lower() in domain_a.lower() or pair["domain_b"].lower() in domain_b.lower():
                    best = pair
                    break
            # Update pair to match actual chosen domains
            best = dict(best)
            best["domain_a"] = domain_a
            best["domain_b"] = domain_b
            connections = [best]

        result = {
            "connections": connections,
            "domain_a": domain_a,
            "domain_b": domain_b,
            "topic": topic or None,
            "cached_at": datetime.now().isoformat(),
        }
        _mc_set(cache_key, result, _TTL_CROSS)
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return JSONResponse(content=result, headers={"Cache-Control": "max-age=7200, private"})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Cross-domain failed: {e}")


@app.get("/recap", dependencies=_auth)
async def recap(refresh: bool = False):
    """Weekly recap: what Ralph learned and did in the last 7 days. Cached 6h."""
    import asyncio, json
    from pathlib import Path
    from datetime import datetime, timedelta

    cache_path = Path.home() / ".neuron" / "recap_cache.json"

    if refresh:
        try:
            cache_path.unlink(missing_ok=True)
        except Exception:
            pass

    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(hours=6):
                return JSONResponse(content=cached, headers={"Cache-Control": "max-age=21600, private"})
        except Exception:
            pass

    try:
        loop = asyncio.get_event_loop()
        engine = get_engine()
        result = await loop.run_in_executor(None, engine.recap)
        result["cached_at"] = datetime.now().isoformat()
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return JSONResponse(content=result, headers={"Cache-Control": "max-age=21600, private"})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Recap failed: {e}")


@app.get("/recap/topics", dependencies=_auth)
def recap_topics():
    """Return all topics/subjects the user has notes on, with chunk count and last-updated date.
    Useful for the practice view topic list."""
    from datetime import datetime
    store = get_store()

    cache_key = "recap:topics"
    cached = _mc_get(cache_key)
    if cached:
        return cached

    try:
        # Pull all metadata from the store to aggregate by topic/title
        results = store.collection.get(include=["metadatas"])
        metadatas = results.get("metadatas") or []
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Could not read store: {e}")

    # Aggregate by (source, title) pairs
    topic_map: dict = {}
    for meta in metadatas:
        title = meta.get("title") or meta.get("source") or "Unknown"
        source = meta.get("source", "")
        key = f"{source}::{title}"
        date_str = (
            meta.get("date")
            or meta.get("created_at")
            or meta.get("source_date")
            or ""
        )
        if key not in topic_map:
            topic_map[key] = {
                "topic": title,
                "source": source,
                "count": 0,
                "last_updated": date_str,
            }
        topic_map[key]["count"] += 1
        # Keep the latest date
        existing = topic_map[key]["last_updated"]
        if date_str and date_str > existing:
            topic_map[key]["last_updated"] = date_str

    topics = sorted(topic_map.values(), key=lambda x: x["last_updated"] or "", reverse=True)
    result = {"topics": topics, "total": len(topics)}
    _mc_set(cache_key, result, ttl_seconds=600)
    return result


@app.get("/timeline", dependencies=_auth)
def timeline(weeks: int = 16, days: int = 0, refresh: bool = False):
    """Learning activity grouped by week + flat events list. Cached 1 hour.

    Query params:
      weeks   -- lookback in weeks (default 16; ignored when days > 0)
      days    -- explicit lookback in days (overrides weeks when > 0)
      refresh -- bust cache and recompute
    """
    import json
    from pathlib import Path
    from datetime import datetime, timedelta

    # Cache key includes the effective period so different window sizes cache independently
    effective_days = days if days > 0 else weeks * 7
    cache_path = Path.home() / ".neuron" / f"timeline_cache_{effective_days}d.json"

    if refresh:
        neuron_dir = Path.home() / ".neuron"
        for p in neuron_dir.glob("timeline_cache_*.json"):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            (neuron_dir / "timeline_cache.json").unlink(missing_ok=True)
        except Exception:
            pass

    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(hours=1):
                return JSONResponse(content=cached, headers={"Cache-Control": "max-age=3600, private", "X-Cache": "FILE"})
        except Exception:
            pass

    try:
        result = get_engine().timeline(weeks=weeks, days=days)
        result["cached_at"] = datetime.now().isoformat()
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return JSONResponse(content=result, headers={"Cache-Control": "max-age=3600, private"})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Timeline failed: {e}")


@app.get("/suggestions", dependencies=_auth)
async def suggestions(refresh: bool = False):
    """Return 4 personalized question suggestions based on recent KB content. Cached 30 min."""
    import asyncio, json, re
    from pathlib import Path
    from datetime import datetime, timedelta

    cache_path = Path.home() / ".neuron" / "suggestions_cache.json"

    if refresh:
        _mc_delete("suggestions")
        try:
            cache_path.unlink(missing_ok=True)
        except Exception:
            pass

    if not refresh:
        # 1. In-memory cache
        cached_mem = _mc_get("suggestions")
        if cached_mem:
            return JSONResponse(content=cached_mem, headers={"Cache-Control": "max-age=1800, private", "X-Cache": "MEM"})
        # 2. File cache
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
                if datetime.now() - cached_at < timedelta(minutes=30):
                    _mc_set("suggestions", cached, _TTL_SUGGESTIONS)
                    return JSONResponse(content=cached, headers={"Cache-Control": "max-age=1800, private", "X-Cache": "FILE"})
            except Exception:
                pass

    try:
        engine = get_engine()
        store = get_store()
        if store.count() == 0:
            return {"suggestions": []}

        # Sample recent content first (last 14 days), then fill from broader searches
        import random
        from datetime import timedelta
        from ..retrieval.engine import _extract_date
        EXCLUDE = {"calendar"}

        # Recent-biased seeds — pull from what Ralph actually touched lately
        # Current semester: OS, Networks, Algorithms, Accounting
        RECENT_SEEDS = [
            "operating systems process memory scheduling virtual machine",
            "computer networks TCP IP routing protocols",
            "algorithms complexity sorting graph dynamic programming",
            "financial accounting balance sheet GAAP income",
            "Columbia coursework homework assignment",
            "meeting discussion decision action item",
            "personal note idea reflection",
        ]
        # Breadth seeds to ensure diversity
        BREADTH_SEEDS = [
            "book highlights reading insight",
            "code programming algorithm implementation",
            "career internship project work",
        ]

        cutoff_recent = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
        seen_titles: set[str] = set()
        recent_sample: list = []
        broad_sample: list = []

        for seed in RECENT_SEEDS:
            try:
                res = store.search(seed, n_results=8)
                for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
                    src = meta.get("source", "")
                    if src in EXCLUDE:
                        continue
                    t = meta.get("title", "")
                    if t in seen_titles:
                        continue
                    seen_titles.add(t)
                    date_str = _extract_date(meta)
                    if date_str and date_str >= cutoff_recent:
                        recent_sample.append((doc, meta))
                    else:
                        broad_sample.append((doc, meta))
            except Exception:
                continue

        for seed in BREADTH_SEEDS:
            try:
                res = store.search(seed, n_results=5)
                for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
                    src = meta.get("source", "")
                    if src in EXCLUDE:
                        continue
                    t = meta.get("title", "")
                    if t in seen_titles:
                        continue
                    seen_titles.add(t)
                    broad_sample.append((doc, meta))
            except Exception:
                continue

        # Prefer recent content; pad with broader sample up to 25 items
        random.shuffle(recent_sample)
        random.shuffle(broad_sample)
        sample = (recent_sample[:15] + broad_sample)[:25]

        if not sample:
            return {"suggestions": []}

        # Tag each item as ACTIVE (user wrote/edited) or PASSIVE (saved/received/course material)
        _active_sources = {"note", "apple_notes", "voice_memo", "notion", "github", "granola"}
        _passive_sources = {"canvas", "gmail", "readwise", "pocket", "youtube", "youtube_liked",
                             "spotify", "podcast", "kindle", "web", "url"}

        def _engagement_tag(meta: dict) -> str:
            src = meta.get("source", "")
            if src in _active_sources:
                return "ACTIVE"  # user wrote/edited this
            if src in _passive_sources:
                return "PASSIVE"  # user received/saved this — may not have read deeply
            return "ACTIVE"

        ctx = "\n\n".join(
            f"[{_engagement_tag(m)} | {m.get('source','')} | {_extract_date(m) or 'no date'}] {m.get('title','')}: {d[:200]}"
            for d, m in sample
        )

        _ctx_captured = ctx
        _engine_captured = engine
        loop = asyncio.get_event_loop()
        _t0_suggestions = time.perf_counter()
        raw = await loop.run_in_executor(None, lambda: _engine_captured._chat(
            "You generate short, specific question suggestions for Ralph — a Columbia CS student taking OS, Networks, Algorithms, and Financial Accounting.\n"
            "He has two kinds of content:\n"
            "- ACTIVE items: things he actually wrote, built, or edited. He knows this material.\n"
            "- PASSIVE items: course readings, saved articles, videos. He may NOT have studied these deeply.\n\n"
            "Generate exactly 5 questions that would actually lead to useful, specific answers in his knowledge base. These should feel personal and timely — not generic.\n\n"
            "Mix these types:\n"
            "- 1-2 ACTIVE questions: 'What did you mean by X in [specific note]?' or 'How does your approach in [project] handle Y?'\n"
            "- 1-2 PASSIVE/discovery questions: frame as exploration — 'Want to understand X from [specific title]?' or 'Your [course] has a reading on X — curious what it argues?'\n"
            "- 1 CONNECTION question: something that bridges two things he's studied — 'How does X from your OS notes connect to Y from your Networks material?'\n\n"
            "Rules:\n"
            "- Each question under 70 characters\n"
            "- Reference actual titles and course names from the items — not generic topics\n"
            "- Questions should be ones he'd actually want to ask — not homework prompts\n"
            "- For upcoming exams: include at least 1 exam-prep question if exam content is in the items\n"
            "- Return ONLY a JSON array of exactly 5 strings. No markdown, no labels.\n\n"
            f"KNOWLEDGE ITEMS:\n{_ctx_captured}",
            max_tokens=400,
            model="claude-haiku-4-5-20251001",
        ))
        logger.info("[TIMING] /suggestions LLM call: %.1fs", time.perf_counter() - _t0_suggestions)
        m = re.search(r'\[[\s\S]*?\]', raw)
        suggestions_list = []
        if m:
            try:
                suggestions_list = [s for s in json.loads(m.group(0)) if isinstance(s, str)][:5]
            except Exception:
                pass

        # --- Build recommendations: 1 book, 1 article, 1 podcast ---
        recommendations: list[dict] = []

        # Book: find a goodreads "Want to read" book connected to current topics
        CURRENT_TOPICS = ["operating systems", "computer networks", "algorithms", "accounting"]
        try:
            want_res = store.collection.get(
                where={"source": {"$eq": "goodreads"}},
                include=["metadatas", "documents"],
                limit=2000,
            )
            want_books = []
            for doc_b, meta_b in zip(want_res.get("documents", []), want_res.get("metadatas", [])):
                status = (meta_b.get("status") or meta_b.get("shelf") or "").lower()
                if "want" in status or "to-read" in status or "to_read" in status:
                    want_books.append((doc_b, meta_b))
            if want_books:
                # Pick the book most semantically relevant to current topics
                best_book = None
                topic_query = " ".join(CURRENT_TOPICS)
                try:
                    bk_res = store.search(topic_query + " book read", n_results=30)
                    want_titles_set = {meta_b.get("title", "").lower() for _, meta_b in want_books}
                    for bk_doc, bk_meta in zip(bk_res["documents"][0], bk_res["metadatas"][0]):
                        if bk_meta.get("source") == "goodreads":
                            bk_status = (bk_meta.get("status") or bk_meta.get("shelf") or "").lower()
                            if "want" in bk_status or "to-read" in bk_status or "to_read" in bk_status:
                                best_book = (bk_doc, bk_meta)
                                break
                except Exception:
                    pass
                if best_book is None and want_books:
                    import random as _rand
                    best_book = _rand.choice(want_books)
                if best_book:
                    bk_title  = best_book[1].get("title", "")
                    bk_author = best_book[1].get("author", "")
                    bk_q      = (bk_title + " " + bk_author).strip().replace(" ", "+")
                    bk_link   = f"https://www.goodreads.com/search?q={bk_q}"
                    doc_lower = (best_book[0] + " " + bk_title).lower()
                    if "algorithm" in doc_lower or "graph" in doc_lower or "complexity" in doc_lower:
                        why_topic = "Algorithms"
                    elif "network" in doc_lower or "protocol" in doc_lower or "tcp" in doc_lower:
                        why_topic = "Networks"
                    elif "os " in doc_lower or "operating" in doc_lower or "process" in doc_lower:
                        why_topic = "OS"
                    elif "account" in doc_lower or "finance" in doc_lower or "gaap" in doc_lower:
                        why_topic = "Accounting"
                    else:
                        why_topic = "your current courses"
                    recommendations.append({
                        "type": "book",
                        "title": bk_title,
                        "why": f"On your Want to Read shelf — connects to {why_topic} themes you're studying this semester.",
                        "link": bk_link,
                    })
        except Exception:
            pass

        # Article: pick a resource based on most relevant current topic from sample
        try:
            topic_counts = {"os": 0, "networks": 0, "algorithms": 0, "accounting": 0}
            for _, s_meta in sample:
                combined = (s_meta.get("title", "") + " " + s_meta.get("course_name", "")).lower()
                if any(k in combined for k in ("operating system", "process", "thread", "memory", "scheduling", " os ")):
                    topic_counts["os"] += 1
                if any(k in combined for k in ("network", "tcp", "routing", "protocol", "socket")):
                    topic_counts["networks"] += 1
                if any(k in combined for k in ("algorithm", "complexity", "sorting", "graph", "dynamic programming")):
                    topic_counts["algorithms"] += 1
                if any(k in combined for k in ("account", "gaap", "balance sheet", "income", "financial")):
                    topic_counts["accounting"] += 1
            top_topic = max(topic_counts, key=lambda k: topic_counts[k])
            ARTICLE_MAP = {
                "os": {
                    "title": "Operating Systems: Three Easy Pieces (OSTEP)",
                    "why": "Free online OS textbook used at many universities — great for filling gaps before exams.",
                    "link": "https://pages.cs.wisc.edu/~remzi/OSTEP/",
                },
                "networks": {
                    "title": "Kurose & Ross — Computer Networking: A Top-Down Approach (companion site)",
                    "why": "Official companion to the Kurose textbook with slides, labs, and Wireshark exercises.",
                    "link": "https://gaia.cs.umass.edu/kurose_ross/index.php",
                },
                "algorithms": {
                    "title": "Visualgo — Algorithm Visualizations",
                    "why": "Interactive visualizations for sorting, graph, and DP algorithms — useful for exam prep.",
                    "link": "https://visualgo.net/en",
                },
                "accounting": {
                    "title": "AccountingCoach — Free Accounting Explanations",
                    "why": "Clear explanations of GAAP concepts, financial statements, and accounting terminology.",
                    "link": "https://www.accountingcoach.com/",
                },
            }
            art = ARTICLE_MAP.get(top_topic)
            if art:
                recommendations.append({
                    "type": "article",
                    "title": art["title"],
                    "why": art["why"],
                    "link": art["link"],
                })
        except Exception:
            pass

        # Podcast: look for a spotify/podcast chunk and surface it
        try:
            pod_res = store.search("podcast episode interview discussion", n_results=20)
            pod_entry = None
            for pod_doc, pod_meta in zip(pod_res["documents"][0], pod_res["metadatas"][0]):
                if pod_meta.get("source") in ("spotify", "podcast"):
                    pod_entry = (pod_doc, pod_meta)
                    break
            if pod_entry:
                pod_title  = pod_entry[1].get("title", pod_entry[1].get("episode", ""))
                pod_show   = pod_entry[1].get("show", pod_entry[1].get("podcast", ""))
                pod_url    = pod_entry[1].get("url", pod_entry[1].get("link", ""))
                if not pod_url:
                    pod_q   = (pod_title + " " + pod_show).strip().replace(" ", "+")
                    pod_url = f"https://open.spotify.com/search/{pod_q}"
                recommendations.append({
                    "type": "podcast",
                    "title": pod_title or pod_show,
                    "why": f"From your saved podcasts{(' — ' + pod_show) if pod_show and pod_show not in pod_title else ''}. Worth a listen.",
                    "link": pod_url,
                })
        except Exception:
            pass

        result = {
            "suggestions": suggestions_list,
            "recommendations": recommendations,
            "cached_at": datetime.now().isoformat(),
        }
        _mc_set("suggestions", result, _TTL_SUGGESTIONS)
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return JSONResponse(content=result, headers={"Cache-Control": "max-age=1800, private"})
    except Exception as e:
        return {"suggestions": [], "recommendations": []}


def _classify_source_type(source: str) -> str:
    """Map raw source slug to a human-readable source_type category."""
    src = source.lower()
    if src in ("goodreads", "kindle", "book", "readwise"):
        return "book"
    if "youtube" in src:
        return "youtube"
    if src in ("web", "url", "browser", "chrome"):
        return "web"
    if src in ("apple_notes", "note", "canvas"):
        return "note"
    if "voice" in src or "whisper" in src or "transcript" in src:
        return "voice"
    return "note"  # sensible default


def _similar_content(a: str, b: str, threshold: float = 0.75) -> bool:
    """Return True if two content strings share more than `threshold` of their tokens."""
    if not a or not b:
        return False
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return False
    overlap = len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))
    return overlap >= threshold


@app.get("/search", dependencies=_auth)
def search(q: str, n: int = 8, source: str = "", offset: int = 0):
    """Hybrid BM25 + vector search with optional source filter and pagination.

    Query params:
      q      — search query (required)
      n      — number of results per page (default 8)
      source — filter by source slug: apple_notes, notes, canvas, books, calendar, web (optional)
      offset — result offset for pagination (default 0)

    Each result includes:
      snippet        — 200-char plain-text preview
      source_type    — book | web | note | voice | youtube
      created_at     — timestamp if available
      relevance_score — float 0-1 normalised from composite score
    """
    # Check cache (TTL = 10 minutes)
    _search_cache_key = f"search:{q.lower().strip()}:{n}:{offset}:{source.lower()}"
    _search_cached = _mc_get(_search_cache_key)
    if _search_cached is not None:
        return _search_cached

    engine = get_engine()
    # Use the engine's full hybrid search (BM25 + vector + RRF) for much better recall
    scored = engine._hybrid_search(q, n_candidates=max(200, (n + offset) * 6))

    # Optional source filter — support friendly aliases
    SOURCE_ALIASES: dict = {
        "notes": ["apple_notes", "note"],
        "apple_notes": ["apple_notes", "note"],
        "canvas": ["canvas"],
        "books": ["goodreads", "kindle", "book", "readwise"],
        "goodreads": ["goodreads", "kindle", "book", "readwise"],
        "calendar": ["google_calendar", "calendar"],
        "web": ["web", "url", "youtube"],
    }
    filter_sources: list[str] = []
    if source:
        filter_sources = SOURCE_ALIASES.get(source.lower(), [source.lower()])

    # Normalise scores to 0-1 range for relevance_score field
    raw_scores = [composite for composite, _doc, _meta, _id in scored]
    _score_max = max(raw_scores) if raw_scores else 1.0
    _score_min = min(raw_scores) if raw_scores else 0.0
    _score_range = (_score_max - _score_min) or 1.0

    items = []
    seen_titles: set[str] = set()
    seen_snippets: list[str] = []  # for near-duplicate content filtering

    for composite, doc, meta, doc_id in scored:
        src = meta.get("source", "")
        if filter_sources and src.lower() not in filter_sources:
            continue
        title = meta.get("title", "")
        key = f"{src}::{title}"
        if key in seen_titles:
            continue

        # Dedup chunks with very similar content from the same source
        snippet_check = doc[:300]
        is_dup = any(_similar_content(snippet_check, prev) for prev in seen_snippets[-20:])
        if is_dup:
            continue

        seen_titles.add(key)
        seen_snippets.append(snippet_check)

        created_at = (
            meta.get("created_at")
            or meta.get("date")
            or meta.get("source_date")
            or meta.get("date_read")
            or ""
        )

        # Build 200-char snippet — strip markdown/whitespace noise
        _raw_snippet = doc.strip().replace("\n", " ")
        snippet = _raw_snippet[:200] + ("\u2026" if len(_raw_snippet) > 200 else "")

        relevance_score = round((composite - _score_min) / _score_range, 4)

        items.append({
            "content": doc,
            "content_preview": doc[:400] + "\u2026" if len(doc) > 400 else doc,
            "snippet": snippet,
            "title": title,
            "source": src,
            "source_type": _classify_source_type(src),
            "created_at": created_at,
            "date": meta.get("date") or meta.get("created_at") or meta.get("source_date") or "",
            "url": meta.get("url") or meta.get("link") or "",
            "composite_score": round(composite, 3),
            "relevance_score": relevance_score,
        })

    total = len(items)
    page_items = items[offset: offset + n]
    _search_result = {
        "results": page_items,
        "query": q,
        "total": total,
        "offset": offset,
        "has_more": (offset + n) < total,
    }
    _mc_set(_search_cache_key, _search_result, ttl_seconds=600)
    return _search_result


# ── LIBRARY ──────────────────────────────────────────────────────────────────

# Legacy dicts kept for library_add_book cache-bust; the primary cache is now _mem_cache.
_library_cache: dict = {}
_connections_cache: dict = {}  # title -> {data, ts}
CONNECTIONS_TTL = 300  # 5 min


@app.get("/library", dependencies=_auth)
def library(shelf: str = "", refresh: bool = False):
    """Return all books from goodreads/kindle/book sources."""
    cache_key = f"library_{shelf}"
    if not refresh:
        cached_mem = _mc_get(cache_key)
        if cached_mem is not None:
            return cached_mem

    store = get_store()

    # Pull all goodreads + kindle chunks via $in operator
    try:
        results = store.collection.get(
            where={"source": {"$in": ["goodreads", "kindle", "book"]}},
            include=["metadatas", "documents"],
            limit=5000,
        )
    except Exception:
        # Fallback: get all and filter client-side
        results = store.collection.get(include=["metadatas", "documents"], limit=10000)
        idxs = [i for i, m in enumerate(results["metadatas"]) if m.get("source") in ("goodreads", "kindle", "book")]
        results["metadatas"] = [results["metadatas"][i] for i in idxs]
        results["documents"] = [results["documents"][i] for i in idxs]

    # Deduplicate by title — metadata already has title/status/rating/date
    books_map: dict = {}
    for doc, meta in zip(results["documents"], results["metadatas"]):
        title = meta.get("title", "")
        if not title:
            # Try to extract from document prefix
            for line in doc.split("\n"):
                stripped = line.strip()
                if stripped and not stripped.startswith("["):
                    title = stripped
                    break
        if not title:
            continue

        # Prefer metadata fields; fall back to document parsing
        status = meta.get("status", "")
        try:
            rating = int(meta.get("rating", 0) or 0)
        except (ValueError, TypeError):
            rating = 0

        if not status:
            for line in doc.split("\n"):
                if line.startswith("Status:"):
                    status = line.replace("Status:", "").strip()
                    break

        if not status:
            status = "unknown"

        date_val = meta.get("date_read") or meta.get("date") or meta.get("created_at", "")
        source_val = meta.get("source", "goodreads")
        author_val = meta.get("author", "")

        if title not in books_map or rating > books_map[title].get("rating", 0):
            books_map[title] = {
                "title": title,
                "author": author_val,
                "status": status,
                "rating": rating,
                "source": source_val,
                "date": date_val,
                "cover_url": (
                    f"https://covers.openlibrary.org/b/isbn/{meta.get('isbn')}-M.jpg"
                    if meta.get("isbn")
                    else None
                ),
            }

    all_books = list(books_map.values())

    # Apply shelf filter
    shelf_map = {
        "read": "Read",
        "reading": "Currently reading",
        "want": "Want to read",
    }
    if shelf and shelf in shelf_map:
        books = [b for b in all_books if b["status"] == shelf_map[shelf]]
    else:
        books = all_books

    # Sort: read first (by rating desc), then reading, then want-to-read
    order = {"Read": 0, "Currently reading": 1, "Want to read": 2, "unknown": 3}
    books.sort(key=lambda b: (order.get(b["status"], 3), -(b.get("rating") or 0)))

    counts = {
        "read": sum(1 for b in all_books if b["status"] == "Read"),
        "reading": sum(1 for b in all_books if b["status"] == "Currently reading"),
        "want": sum(1 for b in all_books if b["status"] == "Want to read"),
        "total": len(all_books),
    }

    result = {"books": books, "counts": counts}
    _mc_set(cache_key, result, _TTL_LIBRARY)
    _library_cache[cache_key] = result  # keep legacy dict in sync for cache-bust
    return result


@app.post("/library/book", dependencies=_auth)
def library_add_book(body: dict = Body(...)):
    """Add a new book or update status/notes/rating."""
    import hashlib
    import time as _time
    from ..cli import chunk_text

    title  = (body.get("title") or "").strip()
    status = body.get("status", "Want to read")
    rating = body.get("rating", 0)
    notes  = body.get("notes", "")
    review = body.get("review", "")
    author = (body.get("author") or "").strip()
    if not title:
        raise HTTPException(400, "title required")

    lines = [f"[GOODREADS BOOK] {title}", f"Status: {status}"]
    if author:
        lines.append(f"Author: {author}")
    if rating:
        lines.append(f"My rating: {rating}/5 " + "★" * int(rating) + "☆" * (5 - int(rating)))
    if review:
        lines.append(f"\nMy review: {review}")
    if notes:
        lines.append(f"\nNotes: {notes}")
    content = "\n".join(lines)

    doc_id = "goodreads_" + hashlib.md5(title.encode()).hexdigest()[:12]
    store = get_store()
    chunks, metas, ids = [], [], []
    for i, chunk in enumerate(chunk_text(content)):
        chunks.append(f"[GOODREADS: {title}]\n\n{chunk}")
        meta: dict = {
            "source": "goodreads",
            "title": title,
            "status": status,
            "rating": str(rating),
            "date": _time.strftime("%Y-%m-%d"),
            "type": "book",
        }
        if author:
            meta["author"] = author
        metas.append(meta)
        ids.append(f"{doc_id}_c{i}")
    store.upsert(chunks, metas, ids)

    # Bust library cache (both legacy dict and unified mem cache)
    global _library_cache
    _library_cache = {}
    _mc_delete_prefix("library_")
    return {"ok": True, "title": title, "chunks": len(chunks)}


@app.post("/library/ask", dependencies=_auth)
def library_ask(body: dict = Body(...)):
    """Ask a question scoped to books the user has read."""
    q = body.get("q", "").strip()
    book = body.get("book", "")
    if not q:
        raise HTTPException(400, "q required")

    engine = get_engine()
    scope = "books I've read" if not book else f'the book "{book}"'
    full_q = f"{q} (scope: {scope})"
    result = engine.ask(full_q)
    return result


@app.get("/library/connections/{book_title:path}", dependencies=_auth)
def library_connections(book_title: str):
    """Find cross-domain connections for a specific book."""
    import time as _time
    global _connections_cache
    now = _time.time()
    cached = _connections_cache.get(book_title)
    if cached and (now - cached["ts"]) < CONNECTIONS_TTL:
        return cached["data"]

    store = get_store()
    engine = get_engine()

    # Search for the book in goodreads
    try:
        res = store.search(book_title, n_results=3, where={"source": {"$in": ["goodreads", "kindle", "book"]}})
        book_chunks = [
            {"content": doc, "title": meta.get("title", ""), "source": meta.get("source", "")}
            for doc, meta in zip(res["documents"][0], res["metadatas"][0])
        ]
    except Exception:
        book_chunks = []

    if not book_chunks:
        raise HTTPException(404, f"Book not found: {book_title}")

    # Use book content to find connections across ALL sources
    book_text = " ".join(c["content"][:200] for c in book_chunks[:2])
    try:
        related_res = store.search(book_text, n_results=15)
        related = [
            {"content": doc, "title": meta.get("title", ""), "source": meta.get("source", "")}
            for doc, meta in zip(related_res["documents"][0], related_res["metadatas"][0])
        ]
    except Exception:
        related = []

    # Filter out the same book
    related = [r for r in related if book_title.lower() not in r.get("title", "").lower()]

    connections = [
        {
            "title": r.get("title", ""),
            "source": r.get("source", ""),
            "excerpt": r.get("content", "")[:200],
        }
        for r in related[:8]
    ]

    result = {"book": book_title, "connections": connections}
    _connections_cache[book_title] = {"data": result, "ts": now}
    return result


# ── Twitter/X live scraping ────────────────────────────────────────────────────
_tw_api = None
_tw_ready = False

def _init_twitter() -> bool:
    """Initialize twscrape with credentials from .env. Returns True if ready."""
    global _tw_api, _tw_ready
    if _tw_ready:
        return _tw_api is not None
    _tw_ready = True
    import os as _os
    username = _os.getenv("TWITTER_USERNAME", "").strip()
    password = _os.getenv("TWITTER_PASSWORD", "").strip()
    email    = _os.getenv("TWITTER_EMAIL", "").strip()
    if not (username and password):
        return False
    try:
        import asyncio, twscrape
        from pathlib import Path as _Path
        db_path = str(_Path.home() / ".neuron" / "twscrape_pool.db")

        async def _setup():
            api = twscrape.API(pool=db_path)
            accounts = await api.pool.get_all()
            if not any(a.username.lower() == username.lower() for a in accounts):
                await api.pool.add_account(username, password, email or f"{username}@gmail.com", password)
                await api.pool.login_all()
            return api

        loop = asyncio.new_event_loop()
        result_api = loop.run_until_complete(_setup())
        loop.close()
        _tw_api = result_api
        return True
    except Exception:
        return False


def _fetch_twitter_live() -> list[dict]:
    """Fetch live tweets via twscrape. Returns [] if not configured or on error."""
    try:
        import asyncio, twscrape, os as _os
        if not _init_twitter() or _tw_api is None:
            return []

        TWITTER_SEARCHES = [
            ("Israel OR Gaza OR Netanyahu breaking", "Israel"),
            ("AI OpenAI Anthropic LLM", "AI"),
            ("breaking news world", "World"),
            ("NBA OR NFL OR sports breaking", "Sports"),
        ]

        async def _run():
            results = []
            for query, category in TWITTER_SEARCHES:
                try:
                    async for tw in _tw_api.search(query, limit=5):
                        if tw.retweetedTweet or tw.quotedTweet:
                            continue  # skip retweets for cleaner signal
                        img = ""
                        if tw.media and tw.media.photos:
                            img = tw.media.photos[0].url
                        results.append({
                            "title": tw.rawContent[:200].strip(),
                            "url": f"https://x.com/{tw.user.username}/status/{tw.id}",
                            "description": tw.rawContent,
                            "image": img,
                            "category": category,
                            "source": f"@{tw.user.username}",
                        })
                except Exception:
                    continue
            return results

        loop = asyncio.new_event_loop()
        items = loop.run_until_complete(_run())
        loop.close()
        return items
    except Exception:
        return []


@app.get("/news", dependencies=_auth)
def news(refresh: bool = False):
    """Fetch fresh news from RSS feeds across tech, AI, world, politics, and Torah. Cached 30 min."""
    import json, re, time
    import xml.etree.ElementTree as ET
    from pathlib import Path
    from datetime import datetime, timedelta
    import httpx

    cache_path = Path.home() / ".neuron" / "news_cache.json"
    summary_cache_path = Path.home() / ".neuron" / "news_summary_cache.json"

    if refresh:
        # Clear both caches
        try:
            cache_path.unlink(missing_ok=True)
            summary_cache_path.unlink(missing_ok=True)
        except Exception:
            pass

    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(minutes=45):
                return cached
        except Exception:
            pass

    RSS_FEEDS = [
        # World / Breaking
        {"url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "category": "World", "label": "NY Times"},
        {"url": "https://feeds.bbci.co.uk/news/world/rss.xml", "category": "World", "label": "BBC World"},
        {"url": "https://feeds.reuters.com/reuters/topNews", "category": "World", "label": "Reuters"},
        {"url": "https://www.aljazeera.com/xml/rss/all.xml", "category": "World", "label": "Al Jazeera"},
        {"url": "https://feeds.reuters.com/reuters/worldNews", "category": "World", "label": "Reuters World"},
        # Israel / Middle East
        {"url": "https://www.timesofisrael.com/feed/", "category": "Israel", "label": "Times of Israel"},
        {"url": "https://www.jta.org/feed", "category": "Israel", "label": "JTA"},
        {"url": "https://www.israelnationalnews.com/Rss.aspx", "category": "Israel", "label": "Arutz Sheva"},
        {"url": "https://www.jpost.com/rss/rssfeedsfrontpage.aspx", "category": "Israel", "label": "Jerusalem Post"},
        {"url": "https://www.ynetnews.com/category/3082", "category": "Israel", "label": "Ynet"},
        # Torah / Jewish Life
        {"url": "https://www.jewishpress.com/feed/", "category": "Torah", "label": "Jewish Press"},
        {"url": "https://www.mishpacha.com/feed/", "category": "Torah", "label": "Mishpacha"},
        {"url": "https://www.chabad.org/tools/rss/rss_parshah.xml", "category": "Torah", "label": "Chabad Parasha"},
        {"url": "https://outorah.org/feed/", "category": "Torah", "label": "OU Torah"},
        # Politics
        {"url": "https://rss.nytimes.com/services/xml/rss/nyt/US.xml", "category": "Politics", "label": "NY Times US"},
        {"url": "https://feeds.npr.org/1001/rss.xml", "category": "Politics", "label": "NPR"},
        {"url": "https://feeds.feedburner.com/politico/CNyl", "category": "Politics", "label": "Politico"},
        # Tech
        {"url": "https://news.ycombinator.com/rss", "category": "Tech", "label": "Hacker News"},
        {"url": "https://www.theverge.com/rss/index.xml", "category": "Tech", "label": "The Verge"},
        {"url": "https://techcrunch.com/feed/", "category": "Tech", "label": "TechCrunch"},
        # AI
        {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "category": "AI", "label": "TechCrunch AI"},
        {"url": "https://openai.com/news/rss.xml", "category": "AI", "label": "OpenAI"},
        {"url": "https://www.anthropic.com/rss", "category": "AI", "label": "Anthropic"},
        {"url": "https://feeds.feedburner.com/oreilly/radar/atom", "category": "AI", "label": "O'Reilly Radar"},
        {"url": "https://huggingface.co/blog/feed.xml", "category": "AI", "label": "HuggingFace"},
        # Finance / Business
        {"url": "https://feeds.bloomberg.com/markets/news.rss", "category": "Finance", "label": "Bloomberg"},
        {"url": "https://www.wsj.com/xml/rss/3_7085.xml", "category": "Finance", "label": "WSJ Markets"},
        {"url": "https://feeds.content.dowjones.io/public/rss/mw_topstories", "category": "Finance", "label": "MarketWatch"},
        # Sports
        {"url": "https://www.espn.com/espn/rss/news", "category": "Sports", "label": "ESPN"},
        {"url": "https://www.espn.com/espn/rss/nba/news", "category": "Sports", "label": "ESPN NBA"},
        {"url": "https://www.espn.com/espn/rss/nfl/news", "category": "Sports", "label": "ESPN NFL"},
        {"url": "https://feeds.bbci.co.uk/sport/rss.xml", "category": "Sports", "label": "BBC Sport"},
        {"url": "https://feeds.bbci.co.uk/sport/basketball/rss.xml", "category": "Sports", "label": "BBC Basketball"},
    ]

    articles = []

    def _parse_rss(feed_info: dict, xml_text: str) -> list[dict]:
        from email.utils import parsedate_to_datetime
        items = []
        try:
            root = ET.fromstring(xml_text)
            ns = {"atom": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/"}
            # Handle both RSS 2.0 and Atom
            channel = root.find("channel")
            feed_items = (channel.findall("item") if channel is not None else []) or root.findall("atom:entry", ns)
            for item in feed_items[:5]:
                title = (
                    (item.find("title").text if item.find("title") is not None else None) or
                    (item.find("atom:title", ns).text if item.find("atom:title", ns) is not None else "")
                )
                link_el = item.find("link")
                link = ""
                if link_el is not None:
                    link = link_el.text or link_el.get("href", "")
                # content:encoded element (WordPress feeds)
                content_encoded = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
                desc_el = item.find("description") or item.find("atom:summary", ns) or item.find("atom:content", ns)
                desc = ""
                if desc_el is not None and desc_el.text:
                    desc = re.sub(r"<[^>]+>", " ", desc_el.text)
                    desc = re.sub(r"\s+", " ", desc).strip()[:300]
                # Better description: fall back to content:encoded if desc still empty
                if not desc and content_encoded is not None and content_encoded.text:
                    desc = re.sub(r"<[^>]+>", " ", content_encoded.text)
                    desc = re.sub(r"\s+", " ", desc).strip()[:300]
                # Pub date normalization
                pub_date_raw = item.find("pubDate") or item.find("atom:updated", ns)
                pub_date = ""
                if pub_date_raw is not None and pub_date_raw.text:
                    try:
                        pub_date = parsedate_to_datetime(pub_date_raw.text).isoformat()
                    except Exception:
                        pub_date = pub_date_raw.text or ""
                # Try to get image — cascade through multiple methods
                image = ""
                # 1. media:thumbnail (Yahoo Media RSS)
                media_thumb = item.find("media:thumbnail", ns)
                if media_thumb is not None:
                    image = media_thumb.get("url", "")
                # 2. media:content
                if not image:
                    media_content = item.find("media:content", ns)
                    if media_content is not None and "image" in (media_content.get("type") or "image"):
                        image = media_content.get("url", "")
                # 3. enclosure
                if not image:
                    enclosure = item.find("enclosure")
                    if enclosure is not None and "image" in (enclosure.get("type") or ""):
                        image = enclosure.get("url", "")
                # 4. Parse img src from description or content:encoded HTML
                if not image:
                    for html_el in [
                        item.find("description"),
                        content_encoded,
                        item.find("atom:content", ns),
                    ]:
                        if html_el is not None and html_el.text:
                            img_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html_el.text)
                            if img_m:
                                image = img_m.group(1)
                                # Skip tiny tracking pixels
                                if image and ("1x1" in image or "pixel" in image.lower() or "track" in image.lower()):
                                    image = ""
                                else:
                                    break
                # 5. Try content:encoded specifically for inline images (WordPress fallback)
                if not image and content_encoded is not None and content_encoded.text:
                    img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', content_encoded.text)
                    if img_match:
                        image = img_match.group(1)

                if title and link:
                    items.append({
                        "title": title.strip(),
                        "url": link.strip(),
                        "description": desc,
                        "image": image,
                        "pub_date": pub_date,
                        "category": feed_info["category"],
                        "source": feed_info["label"],
                    })
        except Exception:
            pass
        return items

    from concurrent.futures import ThreadPoolExecutor, as_completed

    UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"

    def _fetch_feed(feed_info: dict) -> list[dict]:
        import sys, time
        for attempt in range(2):
            try:
                r = httpx.get(feed_info["url"], timeout=8,
                              headers={"User-Agent": "Mozilla/5.0 (compatible; NeuronBot/1.0)"},
                              follow_redirects=True)
                if r.status_code in (429, 503) and attempt == 0:
                    time.sleep(1.5)
                    continue
                if r.status_code == 200:
                    return _parse_rss(feed_info, r.text)
            except Exception as e:
                print(f"Feed error {feed_info['label']}: {e}", file=sys.stderr)
        return []

    # Twitter/X scraping via nitter RSS — try multiple instances until one works
    NITTER_INSTANCES = [
        "nitter.privacydev.net",
        "nitter.1d4.us",
        "nitter.unixfox.eu",
        "nitter.kavin.rocks",
        "xcancel.com",
    ]
    # Accounts and searches relevant to Ralph's interests
    TWITTER_TARGETS = [
        ("user", "TimesofIsrael",  "Israel",   "Times of Israel"),
        ("user", "BreakingILNews", "Israel",   "Breaking IL"),
        ("user", "Haaretz",        "Israel",   "Haaretz"),
        ("user", "BBCBreaking",    "World",    "BBC Breaking"),
        ("user", "Reuters",        "World",    "Reuters Live"),
        ("user", "AnthropicAI",    "AI",       "Anthropic"),
        ("user", "sama",           "AI",       "Sam Altman"),
        ("user", "ESPNBreaking",   "Sports",   "ESPN Breaking"),
        ("search", "Israel Gaza site:twitter.com", "Israel", "X · Israel"),
        ("search", "AI OpenAI Anthropic",          "AI",     "X · AI"),
    ]

    def _fetch_nitter_target(target: tuple) -> list[dict]:
        kind, handle, category, label = target
        for instance in NITTER_INSTANCES:
            try:
                if kind == "user":
                    url = f"https://{instance}/{handle}/rss"
                else:
                    url = f"https://{instance}/search/rss?q={httpx.QueryParams({'q': handle})}&f=tweets"
                with httpx.Client(timeout=5, follow_redirects=True) as c:
                    resp = c.get(url, headers={"User-Agent": UA})
                    if resp.status_code == 200 and "<rss" in resp.text[:200]:
                        feed_info = {"category": category, "label": label}
                        items = _parse_rss(feed_info, resp.text)
                        # Clean up nitter tweet text: strip RT prefix, rewrite links
                        cleaned = []
                        for it in items[:4]:
                            title = it["title"].strip()
                            # Skip retweets and replies
                            if title.startswith("RT ") or title.startswith("R to "):
                                continue
                            it["title"] = title
                            it["source"] = label
                            # nitter URLs — rewrite to twitter.com
                            it["url"] = it["url"].replace(f"https://{instance}/", "https://x.com/")
                            cleaned.append(it)
                        if cleaned:
                            return cleaned
            except Exception:
                continue
        return []

    all_targets = [(f,) for f in RSS_FEEDS]
    with ThreadPoolExecutor(max_workers=len(RSS_FEEDS) + len(TWITTER_TARGETS) + 1) as pool:
        rss_futures  = [pool.submit(_fetch_feed, feed) for feed in RSS_FEEDS]
        tw_futures   = [pool.submit(_fetch_nitter_target, t) for t in TWITTER_TARGETS]
        live_future  = pool.submit(_fetch_twitter_live)
        for fut in as_completed(rss_futures + tw_futures + [live_future]):
            articles.extend(fut.result())

    # Deduplicate by title — exact key match plus fuzzy Jaccard similarity
    seen_keys_short: set[str] = set()
    seen_titles_list: list[str] = []
    deduped: list[dict] = []

    def _title_words(t: str) -> set[str]:
        words = set(re.sub(r"[^a-z0-9\s]", "", t.lower()).split())
        # Strip common stopwords so similarity is content-based
        stopwords = {"the","a","an","is","in","of","to","and","for","on","at","by","with","as","from","that","this","it","its","are","was","has","have","be","will","not","but"}
        return words - stopwords

    for a in articles:
        short_key = re.sub(r"[^a-z0-9]", "", a["title"].lower())[:60]
        if short_key in seen_keys_short:
            continue
        # Fuzzy Jaccard check against recent accepted titles
        words = _title_words(a["title"])
        is_dup = False
        if len(words) >= 4:
            for prev_title in seen_titles_list[-150:]:
                prev_words = _title_words(prev_title)
                if not prev_words:
                    continue
                inter = len(words & prev_words)
                union = len(words | prev_words)
                if union and inter / union >= 0.60:
                    is_dup = True
                    break
        if not is_dup:
            seen_keys_short.add(short_key)
            seen_titles_list.append(a["title"])
            deduped.append(a)
    articles = deduped

    # Compute time_ago for each article
    def _time_ago(pub_date_str: str) -> str:
        if not pub_date_str:
            return ""
        try:
            from datetime import timezone
            dt = datetime.fromisoformat(pub_date_str)
            if dt.tzinfo is not None:
                now = datetime.now(timezone.utc)
            else:
                now = datetime.now()
            diff = now - dt
            secs = int(diff.total_seconds())
            if secs < 0:
                return "just now"
            if secs < 60:
                return "just now"
            if secs < 3600:
                m = secs // 60
                return f"{m}m ago"
            if secs < 86400:
                h = secs // 3600
                return f"{h}h ago"
            if secs < 172800:
                return "Yesterday"
            d = secs // 86400
            return f"{d}d ago"
        except Exception:
            return ""

    for a in articles:
        a["time_ago"] = _time_ago(a.get("pub_date", ""))

    # Prefer articles with images — sort within each category
    articles.sort(key=lambda a: (0 if a.get("image") else 1))

    # Group by category (cap 8 per category)
    by_category: dict[str, list] = {}
    cat_counts: dict[str, int] = {}
    for a in articles:
        c = a["category"]
        if cat_counts.get(c, 0) < 8:
            by_category.setdefault(c, []).append(a)
            cat_counts[c] = cat_counts.get(c, 0) + 1

    # Interleave articles across categories so the flat list is diverse
    # Round-robin through categories sorted by priority
    CATEGORY_ORDER = ["World", "Israel", "Politics", "Torah", "Finance", "Tech", "AI", "Sports"]
    ordered_cats = CATEGORY_ORDER + [c for c in by_category if c not in CATEGORY_ORDER]
    category_iters = {c: iter(by_category[c]) for c in ordered_cats if c in by_category}
    interleaved: list[dict] = []
    while category_iters:
        for cat in list(ordered_cats):
            if cat not in category_iters:
                continue
            try:
                interleaved.append(next(category_iters[cat]))
            except StopIteration:
                del category_iters[cat]
    articles = interleaved[:50]

    result = {
        "articles": articles,
        "by_category": by_category,
        "cached_at": datetime.now().isoformat(),
    }
    cache_path.parent.mkdir(exist_ok=True)
    try:
        cache_path.write_text(json.dumps(result))
    except Exception:
        pass
    return result


@app.get("/news/summary", dependencies=_auth)
def news_summary():
    """Generate AI headline brief from cached news. Cached 30 min alongside news."""
    import json
    from pathlib import Path
    from datetime import datetime, timedelta

    summary_cache_path = Path.home() / ".neuron" / "news_summary_cache.json"
    if summary_cache_path.exists():
        try:
            cached = json.loads(summary_cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(minutes=45):
                return cached
        except Exception:
            pass

    try:
        # Read from news cache file directly — avoid double-fetching if cache is cold
        news_cache_path = Path.home() / ".neuron" / "news_cache.json"
        if not news_cache_path.exists():
            return {"summary": ""}
        try:
            news_data = json.loads(news_cache_path.read_text())
        except Exception:
            return {"summary": ""}
        by_cat = news_data.get("by_category", {})
        if not by_cat:
            return {"summary": ""}

        from datetime import date as _date
        today = _date.today().strftime("%A, %B %-d, %Y")

        # Build headline context — include URLs so LLM can hyperlink them
        ctx_parts = []
        for cat, arts in by_cat.items():
            ctx_parts.append(f"{cat.upper()}:")
            for a in arts[:4]:
                url = a.get("link", "") or a.get("url", "")
                url_part = f" | {url}" if url else ""
                ctx_parts.append(f"  - {a['title']} ({a['source']}){url_part}")
        ctx = "\n".join(ctx_parts)

        engine = get_engine()
        summary_text = engine._chat(
            f"You are writing a personal morning briefing for Ralph — a Columbia University student intensely interested in "
            f"Israel/Middle East (especially current events, IDF, Hamas, geopolitics), Torah/Jewish life (parasha, halacha, Rabbi Avi Harari), "
            f"AI/startups (OpenAI, Anthropic, LLMs), Columbia University, US politics, and finance. Today is {today}.\n\n"
            f"Today's headlines by category:\n{ctx}\n\n"
            f"Write a rich morning briefing with 4 sections (use markdown ## headers). Be substantive — this is the main briefing, not a teaser.\n\n"
            f"## What's Happening\n"
            f"Write in flowing prose paragraphs, not bullet points. Lead with Israel/Middle East if anything is there — give 3-4 sentences covering the key development, who's involved, what it means. "
            f"Include geographic context (country, city, region) for every story you mention. "
            f"If no Israel story, lead with the biggest world or political story. Be specific: names, places, numbers. "
            f"Each major story should be 1-2 sentences. Total section: 4-5 sentences maximum.\n\n"
            f"## The World Today\n"
            f"Write in flowing prose paragraphs. Cover US politics, global events, and anything from the Torah/Jewish category (parasha of the week, a shiur, a Jewish community story). "
            f"Include geographic context for each item. Each major story should be 1-2 sentences. Prioritize what Ralph would actually care about.\n\n"
            f"## Markets & Tech\n"
            f"Write in flowing prose. Cover finance/markets and AI/tech. What moved, who launched what, what's the signal. Be concrete — numbers, names, companies. Each major story 1-2 sentences.\n\n"
            f"## In the Game\n"
            f"If sports stories exist, 1-2 sentences on the key result or storyline. If nothing notable, skip.\n\n"
            f"LINKING RULE: For the single most important article you mention in each section, hyperlink it using the exact article URL from the data with markdown format: [Story Title](URL). "
            f"Only link if a URL is provided in the context. Do not link every sentence — just the 1 key article per section.\n\n"
            f"Rules: 4-5 sentences total per section. Write in flowing prose paragraphs throughout — no bullet points. Be direct and specific. Write like a smart friend who reads everything, not a press release. "
            f"Write in a direct, personal tone. Reference specific headlines by name. Lead with the most important Israel/world story.",
            max_tokens=700,
            model="claude-sonnet-4-6",
        )

        # Clean up the text before caching
        import re as _re
        summary_text = summary_text.strip()
        summary_text = _re.sub(r'\r\n', '\n', summary_text)
        summary_text = _re.sub(r'\n{3,}', '\n\n', summary_text)
        # Strip leading spaces on each line (LLM sometimes indents weirdly)
        summary_text = _re.sub(r'^[ \t]+', '', summary_text, flags=_re.MULTILINE)
        # Remove any stray emoji/replacement chars
        emoji_pat = _re.compile(
            u'[\U0001F300-\U0001F9FF\U00002702-\U000027B0\U0000FE00-\U0000FE0F'
            u'\U00002600-\U000026FF\uFFFD]+', flags=_re.UNICODE)
        summary_text = emoji_pat.sub('', summary_text)

        result = {"summary": summary_text, "cached_at": datetime.now().isoformat()}
        summary_cache_path.parent.mkdir(exist_ok=True)
        try:
            summary_cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return result
    except Exception as e:
        return {"summary": ""}


@app.get("/news/tweets", dependencies=_auth)
def news_tweets(refresh: bool = False):
    """Fetch relevant tweets for today's top news topics via Nitter RSS. Cached 30 min."""
    import json, re, html as html_mod
    from pathlib import Path
    from datetime import datetime, timedelta
    import httpx

    cache_path = Path.home() / ".neuron" / "tweets_cache.json"
    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(minutes=30):
                return cached
        except Exception:
            pass

    # Pull top topics from news cache
    news_cache_path = Path.home() / ".neuron" / "news_cache.json"
    topics = ["Israel Gaza war", "AI artificial intelligence", "US politics", "markets economy"]
    if news_cache_path.exists():
        try:
            nd = json.loads(news_cache_path.read_text())
            by_cat = nd.get("by_category", {})
            topics = []
            for cat in ["Israel", "World", "Politics", "AI", "Finance", "Tech"]:
                arts = by_cat.get(cat, [])
                if arts:
                    # Use first article title as search seed (first 6 words)
                    words = arts[0]["title"].split()[:6]
                    topics.append(" ".join(words))
                if len(topics) >= 4:
                    break
        except Exception:
            pass

    # Nitter RSS instances to try (in order)
    NITTER_INSTANCES = [
        "https://nitter.privacydev.net",
        "https://nitter.poast.org",
        "https://nitter.1d4.us",
    ]

    tweets = []

    def _fetch_nitter_rss(query: str) -> list[dict]:
        """Search Nitter RSS for a query and return tweet dicts."""
        import urllib.parse
        encoded = urllib.parse.quote(query)
        for base in NITTER_INSTANCES:
            try:
                url = f"{base}/search/rss?q={encoded}&f=tweets"
                r = httpx.get(url, timeout=6, follow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code != 200:
                    continue
                import xml.etree.ElementTree as ET
                root = ET.fromstring(r.text)
                channel = root.find("channel")
                if channel is None:
                    continue
                items = channel.findall("item")[:3]
                results = []
                for item in items:
                    title_el = item.find("title")
                    link_el = item.find("link")
                    pubdate_el = item.find("pubDate")
                    desc_el = item.find("description")
                    if title_el is None or link_el is None:
                        continue
                    raw_title = html_mod.unescape(title_el.text or "")
                    # title format is "Username: tweet text"
                    parts = raw_title.split(": ", 1)
                    username = parts[0].strip() if len(parts) > 1 else "Unknown"
                    text = parts[1].strip() if len(parts) > 1 else raw_title
                    # Strip HTML from description if present
                    if desc_el is not None and desc_el.text:
                        text = re.sub(r"<[^>]+>", " ", html_mod.unescape(desc_el.text)).strip()
                        text = re.sub(r"\s+", " ", text)[:280]
                    nitter_link = link_el.text or ""
                    # Convert nitter link → twitter/x.com link
                    twitter_link = re.sub(r"https?://[^/]+/", "https://x.com/", nitter_link)
                    pub_date = pubdate_el.text if pubdate_el is not None else ""
                    results.append({
                        "username": username,
                        "text": text,
                        "url": twitter_link,
                        "nitter_url": nitter_link,
                        "date": pub_date,
                        "topic": query,
                    })
                if results:
                    return results
            except Exception:
                continue
        return []

    seen_urls: set = set()
    for topic in topics[:4]:
        try:
            for t in _fetch_nitter_rss(topic):
                if t["url"] not in seen_urls:
                    seen_urls.add(t["url"])
                    tweets.append(t)
        except Exception:
            continue

    # Fallback: if Nitter failed for all instances, format top headlines as tweet-like cards
    if not tweets:
        if news_cache_path.exists():
            try:
                nd = json.loads(news_cache_path.read_text())
                by_cat = nd.get("by_category", {})
                for cat, arts in list(by_cat.items())[:4]:
                    if arts:
                        a = arts[0]
                        tweets.append({
                            "username": a.get("source", "News"),
                            "text": a.get("title", ""),
                            "url": a.get("link", ""),
                            "date": "",
                            "topic": cat,
                            "is_headline": True,
                        })
            except Exception:
                pass

    result = {"tweets": tweets[:12], "topics": topics, "cached_at": datetime.now().isoformat()}
    cache_path.parent.mkdir(exist_ok=True)
    try:
        cache_path.write_text(json.dumps(result))
    except Exception:
        pass
    return result


@app.get("/recommendations", dependencies=_auth)
def recommendations():
    """Generate personalized study topic recommendations ordered by priority. Cached 15 minutes."""
    import json, re
    from pathlib import Path
    from datetime import datetime, timedelta, date as _date_cls

    # ── In-memory cache (15 min) ──────────────────────────────────────────────
    cached_mem = _mc_get("recommendations")
    if cached_mem:
        return cached_mem

    cache_path = Path.home() / ".neuron" / "recs_cache.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(minutes=15):
                _mc_set("recommendations", cached, _TTL_RECS)
                return cached
        except Exception:
            pass

    try:
        today_d = _date_cls.today()
        today_iso = today_d.isoformat()

        srs_data = _load_srs_data()
        topics_map = srs_data.get("topics", {})

        # ── Build ordered study-topic recommendations ─────────────────────────
        srs_due_recs: list[dict] = []
        recent_add_recs: list[dict] = []
        stale_recs: list[dict] = []
        gap_recs: list[dict] = []

        for _rk, _rt in topics_map.items():
            _name = _rt.get("display_name", _rk)
            _nr = _rt.get("next_review", "")
            _last = _rt.get("last_reviewed", "")
            _ef = _rt.get("ef", 2.5)
            _reps = _rt.get("repetitions", 0)
            _is_due = bool(_nr and _nr <= today_iso)
            _overdue_days = (today_d - _date_cls.fromisoformat(_nr)).days if (_nr and _nr <= today_iso) else 0

            if _is_due:
                srs_due_recs.append({
                    "topic": _name, "key": _rk, "ef": _ef, "reps": _reps,
                    "overdue_days": _overdue_days, "last": _last,
                })
            elif _reps == 0 and not _last:
                recent_add_recs.append({"topic": _name, "key": _rk})
            elif _last and _last < (today_d - timedelta(days=7)).isoformat():
                stale_recs.append({"topic": _name, "key": _rk, "last": _last, "ef": _ef})
            if _ef < 1.8 and not _is_due:
                gap_recs.append({"topic": _name, "key": _rk, "ef": _ef, "last": _last})

        srs_due_recs.sort(key=lambda x: (-x["overdue_days"], x["ef"]))
        stale_recs.sort(key=lambda x: x.get("last", ""))
        gap_recs.sort(key=lambda x: x["ef"])

        # ── Compute streak ────────────────────────────────────────────────────
        streak_days = 0
        _check_day = today_d
        for _ in range(365):
            _day_str = _check_day.isoformat()
            _had = any(
                any(h.get("date", "") == _day_str for h in _rt2.get("history", []))
                for _rt2 in topics_map.values()
            )
            if _had:
                streak_days += 1
                _check_day -= timedelta(days=1)
            else:
                break

        if streak_days < 3:
            streak_context: dict = {
                "streak_days": streak_days,
                "message": "You're building momentum! Start with just 5 minutes today.",
                "task": "Quick review: pick any topic and answer 3 questions.",
                "task_type": "easy",
            }
        elif streak_days >= 7:
            _worst_ef = 9999.0
            _weak_topic = ""
            for _sk, _st in topics_map.items():
                _ef2 = _st.get("ef", 2.5)
                if _ef2 < _worst_ef:
                    _worst_ef = _ef2
                    _weak_topic = _st.get("display_name", _sk)
            streak_context = {
                "streak_days": streak_days,
                "message": f"{streak_days}-day streak — you're on a roll!",
                "task": f"Challenge: deep-dive on '{_weak_topic}' — your toughest topic.",
                "task_type": "challenge",
            }
        else:
            streak_context = {
                "streak_days": streak_days,
                "message": f"{streak_days} days in — keep it going!",
                "task": "Review your SRS due topics.",
                "task_type": "normal",
            }

        # ── Build final ordered list (5-8 items) ──────────────────────────────
        output_recs: list[dict] = []

        for _item in srs_due_recs[:3]:
            _otxt = f"{_item['overdue_days']} day(s) overdue" if _item["overdue_days"] > 0 else "due today"
            output_recs.append({
                "topic": _item["topic"],
                "reason": f"SRS review due — {_otxt}. Reviewing now prevents forgetting.",
                "estimated_time_minutes": 10,
                "priority": "high",
                "source": "srs_due",
            })
        for _item in recent_add_recs[:2]:
            output_recs.append({
                "topic": _item["topic"],
                "reason": "New topic — hasn't been studied yet. First review locks it in.",
                "estimated_time_minutes": 15,
                "priority": "medium",
                "source": "recent_add",
            })
        for _item in stale_recs[:2]:
            _ds = (today_d - _date_cls.fromisoformat(_item["last"])).days if _item.get("last") else 30
            output_recs.append({
                "topic": _item["topic"],
                "reason": f"Last studied {_ds} days ago — revisiting prevents decay.",
                "estimated_time_minutes": 10,
                "priority": "medium",
                "source": "stale",
            })
        _existing_topics = {r["topic"] for r in output_recs}
        for _item in gap_recs[:3]:
            if _item["topic"] not in _existing_topics and len(output_recs) < 8:
                output_recs.append({
                    "topic": _item["topic"],
                    "reason": f"Low mastery (EF={_item['ef']:.2f}) — knowledge gap worth closing.",
                    "estimated_time_minutes": 20,
                    "priority": "low",
                    "source": "knowledge_gap",
                })
                _existing_topics.add(_item["topic"])
        output_recs = output_recs[:8]

        # ── Media recs (books/podcasts/youtube) ───────────────────────────────
        engine = get_engine()
        # Pull a diverse sample of what Ralph is currently engaged with
        SEEDS = [
            "book highlights reading philosophy theology",
            "Israel Middle East politics current events",
            "artificial intelligence machine learning startup",
            "Torah parasha Jewish learning Rabbi",
            "Columbia University course lecture notes",
            "finance economics investing startup venture",
            "podcast episode guest interview",
        ]
        seen: set = set()
        sample: list = []
        for seed in SEEDS:
            try:
                res = engine.store.search(seed, n_results=4)
                for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
                    t = meta.get("title", "")
                    if t and t not in seen:
                        seen.add(t)
                        sample.append(f"[{meta.get('source','')}] {t}: {doc[:200]}")
            except Exception:
                continue

        ctx = "\n\n".join(sample[:28])
        from datetime import date as _date
        today = _date.today().isoformat()

        raw = engine._chat(
            f"Today is {today}. Ralph is a Columbia CS student with deep interests in: Israel/Middle East geopolitics, "
            f"Torah/Jewish learning (esp. Rabbi Avi Harari, and connecting Jewish thought to other domains), "
            f"AI/tech startups (particularly LLMs, Anthropic, OpenAI), philosophy (epistemology, ethics), "
            f"finance/economics, Operating Systems, Algorithms, Computer Networks, and current events.\n\n"
            f"Based on his current knowledge base below, suggest exactly:\n"
            f"- 2 books he should read next — must directly connect to something he's actively studying or thinking about\n"
            f"- 2 podcast episodes worth listening to — specific episodes if possible, not just show names\n"
            f"- 2 YouTube videos or channels — prefer substantive lectures, documentaries, or deep-dive explainers\n\n"
            f"RULES:\n"
            f"- Real titles, real authors/shows only. No hallucinated content.\n"
            f"- For each pick: the 'why' must reference a SPECIFIC thing from his knowledge base — 'connects to your notes on X' or 'builds on what you read in [title]'\n"
            f"- No generic picks — the recommendation should feel like it was made specifically for what he's working on right now\n"
            f"- Books: prefer ones that deepen or challenge something he's already studying\n"
            f"- Podcasts/YouTube: prefer content that would give him a new angle on his current thinking\n\n"
            f"Return ONLY valid JSON:\n"
            f'[{{"type":"book|podcast|youtube","title":"...","author_or_show":"...","why":"1 specific sentence connecting to his KB",'
            f'"search_query":"exact search query to find this","goodreads_query":"for books only"}}]\n\n'
            f"KNOWLEDGE BASE SAMPLE:\n{ctx}",
            max_tokens=1000,
            model="claude-sonnet-4-6",
        )
        m = re.search(r'\[[\s\S]*?\]', raw)
        recs = []
        if m:
            try:
                recs = [r for r in json.loads(m.group(0)) if isinstance(r, dict)]
            except Exception:
                pass

        # Build links
        import urllib.parse
        for rec in recs:
            t = rec.get("type", "")
            q = urllib.parse.quote(rec.get("search_query") or rec.get("title", ""))
            q_book = urllib.parse.quote(rec.get("goodreads_query") or rec.get("title", ""))
            if t == "book":
                rec["link"] = f"https://www.goodreads.com/search?q={q_book}"
                rec["link_label"] = "Goodreads"
                rec["link2"] = f"https://www.amazon.com/s?k={q}"
                rec["link2_label"] = "Amazon"
            elif t == "youtube":
                rec["link"] = f"https://www.youtube.com/results?search_query={q}"
                rec["link_label"] = "YouTube"
            else:
                rec["link"] = f"https://open.spotify.com/search/{q}/podcasts"
                rec["link_label"] = "Spotify"
                rec["link2"] = f"https://podcasts.apple.com/search?term={q}"
                rec["link2_label"] = "Apple Podcasts"

        # ── Add urgency field based on upcoming exams ──────────────────────
        try:
            engine2 = get_engine()
            upcoming_exams_for_recs = engine2.get_upcoming_exams(days=7)
            exam_topics_lower = set()
            has_exam_today = False
            has_exam_this_week = False
            for ex in upcoming_exams_for_recs:
                d = ex.get("days_until", 99)
                topic_g = (ex.get("topic_guess") or ex.get("title") or "").lower()
                exam_topics_lower.add(topic_g)
                if d == 0:
                    has_exam_today = True
                elif d <= 7:
                    has_exam_this_week = True
        except Exception:
            exam_topics_lower = set()
            has_exam_today = False
            has_exam_this_week = False

        for rec in recs:
            why_lower = (rec.get("why") or "").lower()
            title_lower = (rec.get("title") or "").lower()
            combined = why_lower + " " + title_lower
            is_exam_topic = any(et and et in combined for et in exam_topics_lower if et)
            if has_exam_today and is_exam_topic:
                rec["urgency"] = "Today"
            elif has_exam_this_week and is_exam_topic:
                rec["urgency"] = "This week"
            else:
                rec["urgency"] = "When you have time"

        # ── Goodreads cross-reference: surface connections to currently-read books ──
        try:
            store2 = get_store()
            gr_res = store2.collection.get(
                where={"source": {"$eq": "goodreads"}},
                include=["metadatas", "documents"],
                limit=500,
            )
            reading_books = []
            for doc_g, meta_g in zip(gr_res.get("documents", []), gr_res.get("metadatas", [])):
                status = (meta_g.get("status") or meta_g.get("shelf") or "").lower()
                if "read" in status and "want" not in status:
                    reading_books.append({
                        "title": meta_g.get("title", ""),
                        "author": meta_g.get("author", ""),
                        "excerpt": doc_g[:150],
                    })
            for rec in recs:
                if rec.get("type") == "book" and reading_books:
                    rec_title_lower = (rec.get("title") or "").lower()
                    for rb in reading_books[:5]:
                        rb_words = set((rb["title"] + " " + rb["excerpt"]).lower().split())
                        rec_words = set(rec_title_lower.split())
                        if rb_words & rec_words and rb["title"].lower() != rec_title_lower:
                            existing_why = rec.get("why") or ""
                            rec["why"] = existing_why + f" Connects to \'{rb['title']}\' currently in your library."
                            break
        except Exception:
            pass

        result = {
            "recommendations": output_recs,
            "media_recommendations": recs,
            "streak_context": streak_context,
            "srs_due_count": len(srs_due_recs),
            "cached_at": datetime.now().isoformat(),
        }
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        _mc_set("recommendations", result, _TTL_RECS)
        return result
    except Exception:
        return {"recommendations": [], "media_recommendations": [], "streak_context": {}}


@app.get("/focus/now", dependencies=_auth)
def focus_now():
    """High-signal focus areas tailored to Ralph's actual near-term priorities."""
    cache_key = "focus_now"
    cached = _mc_get(cache_key)
    if cached is not None:
        return cached

    engine = get_engine()
    tracks = [
        {
            "id": "datadog",
            "label": "Datadog Ramp",
            "accent": "career",
            "query": "Datadog query planning Apache Arrow Trino ClickHouse Apache Calcite query engine execution planner",
            "action": "Deepen query-engine intuition before your August start date.",
        },
        {
            "id": "systems",
            "label": "Systems Depth",
            "accent": "study",
            "query": "operating systems networks kernel virtual memory page tables scheduling TCP congestion",
            "action": "Keep systems fundamentals sharp for both exams and work.",
        },
        {
            "id": "venture",
            "label": "Founder Track",
            "accent": "long_term",
            "query": "startup venture capital entrepreneurship product market fit Securent YC broker workflow automation",
            "action": "Stay connected to the startup thread without letting it blur today's priorities.",
        },
    ]

    items: list[dict] = []
    for track in tracks:
        try:
            scored = engine._hybrid_search(track["query"], n_candidates=24)
        except Exception:
            continue
        best_doc = ""
        best_meta = None
        for _score, doc, meta, _doc_id in scored:
            if meta.get("source") in {"calendar", "gmail", "spotify"}:
                continue
            best_doc = doc
            best_meta = meta
            break
        if not best_meta:
            continue
        excerpt = " ".join(best_doc.split())
        items.append({
            "id": track["id"],
            "label": track["label"],
            "accent": track["accent"],
            "action": track["action"],
            "query": track["query"],
            "title": best_meta.get("title") or track["label"],
            "source": best_meta.get("source", ""),
            "date": best_meta.get("date") or best_meta.get("created_at") or best_meta.get("ingested_at") or "",
            "excerpt": excerpt[:220] + ("…" if len(excerpt) > 220 else ""),
        })

    payload = {"items": items, "cached_at": datetime.now().isoformat()}
    _mc_set(cache_key, payload, 1800)
    return payload


@app.get("/study-plan", dependencies=_auth)
def study_plan():
    """Generate a weekly study plan based on upcoming exams, SRS due items, and recent activity. Cached 2 hours."""
    import json
    from pathlib import Path
    from datetime import datetime, timedelta, date

    cache_path = Path.home() / ".neuron" / "study_plan_cache.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(hours=2):
                return cached
        except Exception:
            pass

    try:
        engine = get_engine()
        today = date.today()
        today_iso = today.isoformat()

        # Week label
        week_start = today - timedelta(days=today.weekday())
        week_end   = week_start + timedelta(days=6)
        week_label = f"{week_start.strftime('%B %-d')}\u2013{week_end.strftime('%-d, %Y')}"

        # Upcoming exams (next 14 days)
        raw_exams: list[dict] = []
        try:
            raw_exams = engine.get_upcoming_exams(days=14)
        except Exception:
            pass

        exams_out = []
        for ex in raw_exams[:6]:
            days_r = ex.get("days_until", 0)
            try:
                ex_date = date.fromisoformat(ex.get("date", today_iso))
                day_name = ex_date.strftime("%A")
            except Exception:
                day_name = "Upcoming"
            exams_out.append({
                "name": ex.get("title", ""),
                "date": day_name,
                "days_remaining": days_r,
                "topic_guess": ex.get("topic_guess", ""),
            })

        # SRS due — always first priority
        srs_data = _load_srs_data()
        due_topics_sp = sorted(
            [
                {"key": k, "name": t.get("display_name", k), "ef": t.get("ef", 2.5),
                 "overdue_days": (today - date.fromisoformat(t["next_review"])).days}
                for k, t in srs_data.get("topics", {}).items()
                if t.get("next_review") and t["next_review"] <= today_iso
            ],
            key=lambda x: (-x["overdue_days"], x["ef"]),
        )
        srs_due_count = len(due_topics_sp)

        # Compute streak for adaptive planning
        sp_streak_days = 0
        _sp_check = today
        for _ in range(365):
            _sp_day = _sp_check.isoformat()
            _sp_had = any(
                any(h.get("date", "") == _sp_day for h in _t2.get("history", []))
                for _t2 in srs_data.get("topics", {}).values()
            )
            if _sp_had:
                sp_streak_days += 1
                _sp_check -= timedelta(days=1)
            else:
                break

        # Recent activity classification
        week_ago = (today - timedelta(days=7)).isoformat()
        recently_active: list[str] = []
        inactive_topics: list[str] = []
        for k, t in srs_data.get("topics", {}).items():
            last = t.get("last_reviewed", "")
            name = t.get("display_name", k)
            if last and last >= week_ago:
                recently_active.append(name)
            elif last:
                inactive_topics.append(name)

        # Build daily plan (today + 6 days)
        exam_by_day: dict[str, list] = {}
        for ex in exams_out:
            exam_by_day.setdefault(ex["date"], []).append(ex)

        plan_days: list[dict] = []
        near_exams = [ex for ex in exams_out if ex.get("days_remaining", 99) <= 3]

        # Schedule start time — 9:00 AM
        _SCHEDULE_START_HOUR = 9

        for offset in range(7):
            target_date = today + timedelta(days=offset)
            day_name = target_date.strftime("%A")
            exams_this_day = exam_by_day.get(day_name, [])

            schedule: list[dict] = []
            _cur_hour = _SCHEDULE_START_HOUR
            _cur_min = 0

            def _fmt_time(h: int, m: int) -> str:
                suffix = "am" if h < 12 else "pm"
                h12 = h if h <= 12 else h - 12
                if h12 == 0:
                    h12 = 12
                return f"{h12}:{m:02d}{suffix}"

            def _add_slot(activity: str, topic: str, dur: int) -> None:
                nonlocal _cur_hour, _cur_min
                schedule.append({
                    "time": _fmt_time(_cur_hour, _cur_min),
                    "activity": activity,
                    "topic": topic,
                    "duration_min": dur,
                })
                _cur_min += dur
                _cur_hour += _cur_min // 60
                _cur_min = _cur_min % 60

            if exams_this_day:
                topics_list = []
                for ex in exams_this_day:
                    tg = ex.get("topic_guess") or ex.get("name", "")
                    if tg and tg not in topics_list:
                        topics_list.append(tg)
                exam_names_joined = ", ".join(ex["name"] for ex in exams_this_day)
                focus = f"{exam_names_joined} Review"
                duration = 90 if exams_this_day[0].get("days_remaining", 99) <= 1 else 60
                # SRS first even on exam days (quick)
                if due_topics_sp and offset == 0:
                    _n = min(3, srs_due_count)
                    _add_slot(f"SRS review ({_n} topics)", ", ".join(d["name"] for d in due_topics_sp[:_n]), 10)
                for tg in topics_list[:3]:
                    _add_slot("Exam prep", tg, duration // max(1, len(topics_list)))
            elif offset == 0 and due_topics_sp:
                topics_list = [d["name"] for d in due_topics_sp[:4]]
                focus = "SRS Review + Catch Up"
                duration = max(10 * srs_due_count, 30)
                _n = min(4, srs_due_count)
                _add_slot(f"SRS review ({_n} due)", ", ".join(d["name"] for d in due_topics_sp[:_n]), 10 * _n)
                if inactive_topics:
                    _add_slot("Deep review", inactive_topics[0], 20)
                    topics_list.append(inactive_topics[0])
            else:
                # Always include SRS if due
                if due_topics_sp and offset <= 1:
                    _n = min(2, srs_due_count)
                    _add_slot(f"SRS review ({_n} due)", ", ".join(d["name"] for d in due_topics_sp[:_n]), 10)
                if offset <= 2 and inactive_topics:
                    topics_list = inactive_topics[:2]
                    focus = f"Revisit {inactive_topics[0]}"
                    duration = 45
                    _add_slot("Revisit stale topic", inactive_topics[0], 25)
                    if len(inactive_topics) > 1:
                        _add_slot("Revisit stale topic", inactive_topics[1], 20)
                elif recently_active:
                    topics_list = recently_active[:2]
                    focus = f"Deepen {recently_active[0]}"
                    duration = 30
                    _add_slot("Deep study", recently_active[0], 30)
                else:
                    topics_list = ["General review"]
                    focus = "Open study"
                    duration = 30
                    _add_slot("Open review", "any topic", 30)

            if near_exams and offset < 3 and duration < 60:
                duration = 60

            # Adapt duration based on streak
            if sp_streak_days >= 14 and offset == 0:
                duration = min(duration + 15, 120)
            elif sp_streak_days == 0 and offset == 0:
                duration = max(duration - 10, 15)

            plan_days.append({
                "day": day_name,
                "date": target_date.isoformat(),
                "focus": focus,
                "topics": topics_list[:4],
                "duration_min": duration,
                "is_today": offset == 0,
                "schedule": schedule,
            })

        result = {
            "week": week_label,
            "exams": exams_out,
            "plan": plan_days,
            "srs_due": srs_due_count,
            "streak_days": sp_streak_days,
            "today_focus": plan_days[0]["focus"] if plan_days else "",
            "today_duration_min": plan_days[0]["duration_min"] if plan_days else 30,
            "today_topics": plan_days[0]["topics"] if plan_days else [],
            "today_schedule": plan_days[0]["schedule"] if plan_days else [],
            "cached_at": datetime.now().isoformat(),
        }
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return result
    except Exception as _ex:
        import traceback as _tb
        logger.error(f"/study-plan error: {_ex}\n{_tb.format_exc()}")
        return {
            "week": "",
            "exams": [],
            "plan": [],
            "srs_due": 0,
            "streak_days": 0,
            "today_focus": "",
            "today_duration_min": 30,
            "today_topics": [],
            "today_schedule": [],
        }


class StudySessionRequest(BaseModel):
    topic: str | None = None


@app.post("/study-session", dependencies=_auth)
def study_session(request: Request, req: StudySessionRequest = None):
    """Generate a focused study session.

    If a topic is provided, generates a session on that topic.
    Otherwise, detects the soonest upcoming exam from the calendar and focuses there.
    Falls back to SRS-due topics if available, or suggests the most urgent exam topics.
    Returns up to 10 exercises with context about what to focus on."""
    import json, re
    from datetime import date, timedelta
    from ..retrieval.engine import _build_numbered_context

    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip, "/study-session", max_per_minute=5):
        raise HTTPException(status_code=429, detail="Rate limit exceeded: max 5 requests per minute for /study-session.")

    if req is None:
        req = StudySessionRequest()

    today = date.today().isoformat()
    engine = get_engine()

    # --- Detect upcoming exams from calendar (uses cached _upcoming_summary for speed) ---
    upcoming_exams: list[dict] = []
    try:
        upcoming_exams = engine.get_upcoming_exams(days=7)
    except Exception:
        pass

    # --- Determine topics to cover ---
    # Priority: explicit topic request > soonest exam > SRS due > no session
    session_topics: list[str] = []
    exam_context: str = ""
    focus_reason: str = ""

    if req.topic:
        session_topics = [req.topic]
        focus_reason = f"Requested topic: {req.topic}"
    elif upcoming_exams:
        # Focus on the soonest exam; if multiple same day, include all
        soonest_date = upcoming_exams[0]["date"]
        soonest_exams = [e for e in upcoming_exams if e["date"] == soonest_date]
        for e in soonest_exams:
            t = e.get("topic_guess", e["title"])
            if t and t not in session_topics:
                session_topics.append(t)
        days_until = soonest_exams[0].get("days_until", 0)
        exam_names = ", ".join(e["title"] for e in soonest_exams)
        focus_reason = f"Exam in {days_until} day(s): {exam_names}"

        # Build exam context string for the prompt
        lines = ["UPCOMING EXAMS (next 7 days):"]
        for e in upcoming_exams[:6]:
            days_away = e.get("days_until", 0)
            label = "TODAY" if days_away == 0 else f"in {days_away}d"
            lines.append(f"  - {e['title']} ({label})")
        exam_context = "\n".join(lines)
    else:
        # Fall back to SRS-due topics
        srs = _load_srs_data()
        due_topics = [
            t.get("display_name", k)
            for k, t in srs.get("topics", {}).items()
            if t.get("next_review") and t["next_review"] <= today
        ]
        if due_topics:
            session_topics = due_topics[:4]
            focus_reason = f"SRS due: {', '.join(session_topics)}"
        else:
            # Last resort: suggest something useful
            return {
                "exercises": [],
                "topics": [],
                "focus_reason": "No upcoming exams or SRS topics due. Use /practice with a specific topic.",
                "message": "No session topic detected. POST with {\"topic\": \"your topic\"} to start a session.",
                "upcoming_exams": upcoming_exams,
            }

    # --- Generate exercises for each topic ---
    all_exercises: list[dict] = []
    per_topic = max(2, 10 // len(session_topics))

    for topic in session_topics[:5]:  # cap at 5 topics
        try:
            queries = engine._expand_query(topic)
            scored = engine._multi_search(queries, n_candidates=150)[:20]
            if not scored:
                continue
            docs = [x[1] for x in scored]
            metas = [x[2] for x in scored]
            context, _ = _build_numbered_context(docs, metas)
            # Cap context to avoid token overflow — 8000 chars is ~2000 tokens
            context_capped = context[:8000]

            exam_note = f"\n\nCONTEXT: {exam_context}" if exam_context else ""

            raw = engine._chat(
                f'You are generating exam-prep practice for Ralph at Columbia — topic: "{topic}". Today is {today}.{exam_note}\n\n'
                f'Generate exactly {per_topic} high-quality Columbia-exam-caliber exercises drawn from the sources below.\n\n'
                f'These must feel like real exam questions — not trivial definitions, but conceptual understanding, '
                f'mechanism explanations, tradeoff analysis, and synthesis. The kind of question that separates students who truly understand from those who memorized.\n\n'
                f'EXERCISE MIX:\n'
                f'- At least 1 multiple_choice: test a key distinction, algorithm property, or tradeoff. '
                f'  4 options A/B/C/D. Distractors must be plausible — use real misconceptions from this topic.\n'
                f'- At least 1 concept or application question: open-ended, requires explaining a mechanism\n\n'
                f'TOPIC-SPECIFIC GUIDANCE:\n'
                f'- OS: page faults, TLB misses, scheduling decisions, deadlock conditions, inode structure, system calls\n'
                f'- Networks: congestion control phases, TCP handshake, DNS hierarchy, HTTP vs HTTPS, BGP path selection\n'
                f'- Algorithms: DP state transitions, reduction proofs, greedy exchange argument, graph algorithm correctness\n'
                f'- Accounting: debits/credits, revenue recognition timing, inventory methods, financial ratios\n\n'
                f'For multiple_choice: "question" = stem + "\\nA) ...\\nB) ...\\nC) ...\\nD) ..."\n'
                f'"answer" = "B) [correct text]", "options" = ["A) ...","B) ...","C) ...","D) ..."]\n\n'
                f'REQUIREMENTS:\n'
                f'- Reference specific course material, algorithms, or concepts from the sources by name\n'
                f'- "answer": complete and educational — explains the why, not just the what\n'
                f'- "explanation": 2-3 sentences teaching the concept with specifics from the sources\n'
                f'- "source_hint": exact title/course where this appears\n\n'
                f'Return ONLY a JSON array (no markdown):\n'
                f'[{{"type":"multiple_choice|concept|application|synthesis","question":"...","difficulty":"easy|medium|hard",'
                f'"answer":"...","explanation":"...","source_hint":"...","options":null_or_array,"topic":"{topic}"}}]\n\n'
                f'SOURCES:\n{context_capped}',
                max_tokens=4000,
                model="claude-sonnet-4-6",
            )
            m = re.search(r'\[[\s\S]*\]', raw)
            if m:
                try:
                    exercises = json.loads(m.group(0))
                except json.JSONDecodeError:
                    # Truncated response: try to salvage any complete exercise objects
                    exercises = []
                    for partial_match in re.finditer(r'\{[^{}]*"question"[^{}]*\}', raw, re.DOTALL):
                        try:
                            obj = json.loads(partial_match.group(0))
                            if isinstance(obj, dict) and "question" in obj:
                                exercises.append(obj)
                        except Exception:
                            pass
                for ex in exercises:
                    if isinstance(ex, dict):
                        ex.setdefault("options", None)
                        ex["topic"] = topic
                        all_exercises.append(ex)
        except Exception as _ex:
            import traceback as _tb
            logger.error(f"study-session error for topic '{topic}': {_ex}\n{_tb.format_exc()}")
            continue

    if not all_exercises:
        return {
            "exercises": [],
            "topics": session_topics,
            "focus_reason": focus_reason,
            "message": f"No material found for {', '.join(session_topics)}. Try ingesting course notes first.",
            "upcoming_exams": upcoming_exams,
        }

    # Interleave by topic so questions from different topics are mixed
    from_each: dict[str, list] = {}
    for ex in all_exercises:
        from_each.setdefault(ex.get("topic", ""), []).append(ex)

    interleaved: list[dict] = []
    topic_iters_map = {t: iter(exs) for t, exs in from_each.items()}
    while topic_iters_map:
        for t in list(topic_iters_map.keys()):
            try:
                interleaved.append(next(topic_iters_map[t]))
            except StopIteration:
                del topic_iters_map[t]

    return {
        "exercises": interleaved[:10],
        "topics": session_topics,
        "topic_count": len(session_topics),
        "focus_reason": focus_reason,
        "upcoming_exams": upcoming_exams[:6],
    }


@app.get("/learning-report", dependencies=_auth)
def learning_report(refresh: bool = False):
    """Weekly learning report: mastery trends, upcoming exams, knowledge gaps, actionable plan. Cached 4h."""
    import json, re
    from pathlib import Path
    from datetime import datetime, timedelta, date

    cache_path = Path.home() / ".neuron" / "learning_report_cache.json"

    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(hours=4):
                return cached
        except Exception:
            pass

    srs = _load_srs_data()
    topics = srs.get("topics", {})
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()

    # Analyze practice history over the last 7 days
    practiced_this_week: list[dict] = []
    improving: list[str] = []
    struggling: list[str] = []
    due_count = 0

    for key, t in topics.items():
        name = t.get("display_name", key)
        history = t.get("history", [])
        recent = [h for h in history if h.get("date", "") >= week_ago]

        if recent:
            practiced_this_week.append({
                "topic": name,
                "sessions": len(recent),
                "recent_score": recent[-1].get("score", ""),
                "repetitions": t.get("repetitions", 0),
                "mastery": min(100, round((t.get("repetitions", 0) * 12) * (t.get("ef", 2.5) / 2.5))),
            })
            scores = [h.get("score") for h in recent]
            correct_rate = scores.count("correct") / len(scores)
            if correct_rate >= 0.7:
                improving.append(name)
            elif correct_rate <= 0.3:
                struggling.append(name)

        if t.get("next_review") and t["next_review"] <= today:
            due_count += 1

    total_sessions = sum(p["sessions"] for p in practiced_this_week)

    # --- Pull upcoming exams (uses cached _upcoming_summary, much faster than scanning all calendar docs) ---
    upcoming_exams: list[dict] = []
    try:
        engine = get_engine()
        upcoming_exams = engine.get_upcoming_exams(days=14)
    except Exception:
        pass

    # --- Pull recent KB activity to assess what's being studied ---
    recent_kb_summary = ""
    try:
        engine = get_engine()
        store = get_store()
        from ..retrieval.engine import _extract_date
        # Search for course-related content touched recently
        COURSE_SEEDS = [
            "operating systems process thread memory scheduling",
            "computer networks TCP IP routing protocols",
            "algorithms complexity sorting graph dynamic programming",
            "financial accounting balance sheet income statement",
        ]
        seen_titles: set = set()
        recent_items: list = []
        cutoff = (date.today() - timedelta(days=14)).isoformat()
        for seed in COURSE_SEEDS:
            try:
                res = store.search(seed, n_results=5)
                for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
                    t = meta.get("title", "")
                    src = meta.get("source", "")
                    if t and t not in seen_titles and src in ("canvas", "note", "apple_notes", "notion", "file"):
                        seen_titles.add(t)
                        recent_items.append(f"[{src}] {t}")
            except Exception:
                continue
        if recent_items:
            recent_kb_summary = "\n".join(recent_items[:20])
    except Exception:
        pass

    # --- Build AI report (always — use exams + KB if no SRS data) ---
    report_text = ""
    try:
        engine = get_engine()
        exam_lines = "\n".join(
            f"  - {e['title']} (in {e['days_until']} day(s), {e['date']})"
            for e in upcoming_exams[:6]
        ) if upcoming_exams else "  None found"

        if practiced_this_week:
            srs_ctx = "\n".join(
                f"  - {p['topic']}: {p['sessions']} session(s), mastery {p['mastery']}%, "
                f"recent score: {p['recent_score']}"
                for p in practiced_this_week
            )
        else:
            srs_ctx = "  No practice sessions recorded yet this week."

        kb_ctx = recent_kb_summary if recent_kb_summary else "  No recent course material found."

        report_text = engine._chat(
            f"You are Neuron — Ralph's Columbia exam coach. Today is {today}.\n"
            f"Ralph takes Operating Systems, Computer Networks, Algorithms, and Financial Accounting.\n\n"
            f"UPCOMING EXAMS:\n{exam_lines}\n\n"
            f"PRACTICE HISTORY (this week, {total_sessions} total sessions):\n{srs_ctx}\n\n"
            f"COURSE MATERIAL IN KB:\n{kb_ctx}\n\n"
            f"Improving topics: {', '.join(improving) or 'none'}\n"
            f"Struggling topics: {', '.join(struggling) or 'none'}\n"
            f"SRS topics due for review: {due_count}\n\n"
            f"Write a 4-5 sentence learning report that sounds like a coach who looked at the data and has an actual opinion:\n"
            f"1. Lead with the most urgent exam and exactly how many days remain — be direct about the urgency level\n"
            f"2. Based on the practice data and course material, identify the 1-2 topics that need the most attention RIGHT NOW\n"
            f"3. Name 2-3 specific concepts from the course material to prioritize (e.g. 'TLB shootdowns', 'TCP congestion window', 'DP recurrences with memoization')\n"
            f"4. One concrete, specific study action for the next 24 hours — not 'review your notes' but 'work through 3 practice problems on page fault handling'\n\n"
            f"Rules: Direct. Specific. No filler. No emojis. Sound like a coach who read the data, not a bot.\n"
            f"If no practice data, base recommendations on the upcoming exams and course material in the KB.",
            max_tokens=450,
            model="claude-haiku-4-5-20251001",
        )
    except Exception:
        pass

    result = {
        "practiced_this_week": practiced_this_week,
        "total_sessions": total_sessions,
        "improving": improving,
        "struggling": struggling,
        "due_count": due_count,
        "upcoming_exams": upcoming_exams,
        "report": report_text,
        "cached_at": datetime.now().isoformat(),
    }
    cache_path.parent.mkdir(exist_ok=True)
    try:
        cache_path.write_text(json.dumps(result))
    except Exception:
        pass
    return result


@app.get("/export", dependencies=_auth)
def export_notes():
    """Export all knowledge base items as a JSON array of {title, source, text, date, url}. Useful for backup."""
    import json as _json
    from ..retrieval.engine import _extract_date
    store = get_store()
    if store.count() == 0:
        return JSONResponse(content=[], headers={"Content-Disposition": "attachment; filename=neuron_export.json"})
    try:
        result = store.collection.get(include=["documents", "metadatas", "ids"])
        items = []
        seen: set[str] = set()
        for doc_id, doc, meta in zip(result["ids"], result["documents"], result["metadatas"]):
            title = meta.get("title", "")
            source = meta.get("source", "")
            key = f"{source}::{title}"
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "title": title,
                "source": source,
                "text": doc,
                "date": _extract_date(meta),
                "url": meta.get("url", meta.get("source_url", "")),
            })
        items.sort(key=lambda x: (x.get("date") or "", x.get("title") or ""))
        return JSONResponse(
            content=items,
            headers={"Content-Disposition": "attachment; filename=neuron_export.json"},
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Export failed: {e}")


# ── SPACED REPETITION SYSTEM ──────────────────────────────────────────────────

def _load_srs_data() -> dict:
    import json
    from pathlib import Path
    path = Path.home() / ".neuron" / "srs_data.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {"topics": {}}


def _save_srs_data(data: dict):
    import json
    from pathlib import Path
    path = Path.home() / ".neuron" / "srs_data.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _sm2_update(ef: float, interval: int, repetitions: int, score_num: int) -> tuple[float, int, int]:
    """SM-2 algorithm (Ebbinghaus spaced repetition).

    score_num quality scale:
      0-2 → failed recall: reset interval to 1 day, reset repetitions
      3   → correct but hard: interval stays the same (no growth)
      4   → correct with hesitation: interval × 1.3
      5   → perfect recall: interval × 2.5

    EF (easiness factor) update: EF = EF + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)
    Min EF = 1.3
    """
    # EF update applies for all quality scores
    new_ef = ef + (0.1 - (5 - score_num) * (0.08 + (5 - score_num) * 0.02))
    new_ef = max(1.3, round(new_ef, 3))

    if score_num <= 2:
        # Failed recall: reset to beginning
        new_interval = 1
        new_repetitions = 0
    elif score_num == 3:
        # Correct but hard: interval stays the same
        new_interval = max(1, interval)
        new_repetitions = repetitions + 1
    elif score_num == 4:
        # Correct with hesitation
        if repetitions == 0:
            new_interval = 1
        elif repetitions == 1:
            new_interval = 6
        else:
            new_interval = max(1, round(interval * 1.3))
        new_repetitions = repetitions + 1
    else:  # score_num == 5: perfect recall
        if repetitions == 0:
            new_interval = 1
        elif repetitions == 1:
            new_interval = 6
        else:
            new_interval = max(1, round(interval * 2.5))
        new_repetitions = repetitions + 1

    return new_ef, new_interval, new_repetitions


class SRSRecordRequest(BaseModel):
    topic: str
    score: str  # "correct" | "partial" | "incorrect"
    correct_count: int = 0
    total_count: int = 0


@app.post("/srs/record", dependencies=_auth)
def srs_record(req: SRSRecordRequest):
    """Record a practice session and update SRS schedule for this topic."""
    from datetime import date, timedelta
    # SM-2 quality scale: correct=5 (perfect), partial=4 (correct with hesitation), incorrect=1 (failed)
    score_map = {"correct": 5, "partial": 4, "incorrect": 1}
    score_num = score_map.get(req.score, 4)

    data = _load_srs_data()
    topics = data.setdefault("topics", {})
    topic_key = req.topic.lower().strip()

    if topic_key not in topics:
        topics[topic_key] = {
            "ef": 2.5, "interval": 1, "repetitions": 0,
            "last_reviewed": None, "next_review": None,
            "history": [], "display_name": req.topic,
        }

    t = topics[topic_key]
    new_ef, new_interval, new_reps = _sm2_update(
        t.get("ef", 2.5), t.get("interval", 1), t.get("repetitions", 0), score_num
    )
    today = date.today().isoformat()
    next_review = (date.today() + timedelta(days=new_interval)).isoformat()

    t["ef"] = new_ef
    t["interval"] = new_interval
    t["repetitions"] = new_reps
    t["last_reviewed"] = today
    t["next_review"] = next_review
    t["display_name"] = req.topic
    t.setdefault("history", []).append({
        "date": today, "score": req.score,
        "correct_count": req.correct_count, "total_count": req.total_count,
    })
    t["history"] = t["history"][-50:]

    _save_srs_data(data)
    return {"ok": True, "next_review": next_review, "interval_days": new_interval}


class SRSCardRecordRequest(BaseModel):
    card_index: int
    rating: str  # "again" | "hard" | "good" | "easy"


@app.post("/srs/card/record", dependencies=_auth)
def srs_card_record(req: SRSCardRecordRequest):
    """Record a self-rating for a specific flashcard (Again/Hard/Good/Easy) and update its SM-2 schedule."""
    from datetime import date, timedelta
    # Map self-rating labels to SM-2 quality scores (0-5 scale)
    rating_map = {"again": 1, "hard": 2, "good": 4, "easy": 5}
    score_num = rating_map.get(req.rating.lower(), 4)

    data = _load_srs_data()
    cards = data.get("cards", [])
    if req.card_index < 0 or req.card_index >= len(cards):
        raise HTTPException(status_code=404, detail=f"Card index {req.card_index} not found")

    card = cards[req.card_index]
    new_ef, new_interval, new_reps = _sm2_update(
        card.get("ef", 2.5), card.get("interval", 1), card.get("repetitions", 0), score_num
    )
    today = date.today().isoformat()
    next_review = (date.today() + timedelta(days=new_interval)).isoformat()

    card["ef"] = new_ef
    card["interval"] = new_interval
    card["repetitions"] = new_reps
    card["last_reviewed"] = today
    card["next_review"] = next_review
    card.setdefault("history", []).append({"date": today, "rating": req.rating})
    card["history"] = card["history"][-50:]

    data["cards"][req.card_index] = card
    _save_srs_data(data)
    return {"ok": True, "next_review": next_review, "interval_days": new_interval}


@app.get("/srs/due", dependencies=_auth)
def srs_due():
    """Topics and flashcards due for review today."""
    from datetime import date
    today = date.today().isoformat()
    data = _load_srs_data()
    due = []

    # Topic-level SRS entries
    for key, t in data.get("topics", {}).items():
        nr = t.get("next_review")
        if nr and nr <= today:
            due.append({
                "type": "topic",
                "topic": t.get("display_name", key),
                "next_review": nr,
                "repetitions": t.get("repetitions", 0),
                "ef": t.get("ef", 2.5),
                "overdue_days": (date.fromisoformat(today) - date.fromisoformat(nr)).days,
                "last_reviewed": t.get("last_reviewed"),
            })

    # Per-question flashcards (auto-seeded from wrong answers)
    due_cards = []
    for i, card in enumerate(data.get("cards", [])):
        nr = card.get("next_review")
        if nr and nr <= today:
            due_cards.append({
                "type": "flashcard",
                "card_index": i,
                "topic": card.get("topic", ""),
                "question": card.get("question", ""),
                "answer": card.get("answer", ""),
                "explanation": card.get("explanation", ""),
                "next_review": nr,
                "repetitions": card.get("repetitions", 0),
                "ef": card.get("ef", 2.5),
                "overdue_days": (date.fromisoformat(today) - date.fromisoformat(nr)).days,
                "last_reviewed": card.get("last_reviewed"),
            })

    due.sort(key=lambda x: x["overdue_days"], reverse=True)
    due_cards.sort(key=lambda x: x["overdue_days"], reverse=True)

    all_due = due + due_cards

    # Build per-topic summary for topics_list field
    topic_due_map: dict = {}
    for item in all_due:
        topic = item.get("topic", "")
        if not topic:
            continue
        if topic not in topic_due_map:
            topic_due_map[topic] = {
                "topic": topic,
                "due_count": 0,
                "next_due": item.get("next_review"),
                "last_reviewed": item.get("last_reviewed"),
            }
        topic_due_map[topic]["due_count"] += 1
        nr = item.get("next_review")
        if nr and (not topic_due_map[topic]["next_due"] or nr < topic_due_map[topic]["next_due"]):
            topic_due_map[topic]["next_due"] = nr
        lr = item.get("last_reviewed")
        existing_lr = topic_due_map[topic]["last_reviewed"]
        if lr and (not existing_lr or lr > existing_lr):
            topic_due_map[topic]["last_reviewed"] = lr

    topics_list_due = sorted(topic_due_map.values(), key=lambda x: x["due_count"], reverse=True)

    return {
        "due": all_due,
        "count": len(all_due),
        "topic_count": len(due),
        "flashcard_count": len(due_cards),
        "date": today,
        "topics_list": topics_list_due,
    }


@app.get("/srs/stats", dependencies=_auth)
def srs_stats():
    """Per-topic mastery, upcoming reviews, due counts."""
    from datetime import date, timedelta
    today = date.today().isoformat()
    week_ahead = (date.today() + timedelta(days=7)).isoformat()
    data = _load_srs_data()
    topics_list = []
    due_count = 0
    upcoming_7d = 0
    for key, t in data.get("topics", {}).items():
        nr = t.get("next_review", today)
        is_due = bool(nr and nr <= today)
        is_upcoming = bool(nr and today < nr <= week_ahead)
        if is_due:
            due_count += 1
        if is_upcoming:
            upcoming_7d += 1
        reps = t.get("repetitions", 0)
        ef = t.get("ef", 2.5)
        mastery = min(100, round((reps * 12) * (ef / 2.5)))
        history = t.get("history", [])
        recent_scores = [h["score"] for h in history[-5:]]
        topics_list.append({
            "topic": t.get("display_name", key),
            "mastery": mastery,
            "repetitions": reps,
            "next_review": nr,
            "is_due": is_due,
            "ef": round(ef, 2),
            "interval": t.get("interval", 1),
            "recent_scores": recent_scores,
            "last_reviewed": t.get("last_reviewed"),
        })
    topics_list.sort(key=lambda x: (-int(x["is_due"]), -x["mastery"]))
    return {
        "topics": topics_list,
        "due_count": due_count,
        "upcoming_7d": upcoming_7d,
        "total_topics": len(topics_list),
    }


# ── PROGRESS ──────────────────────────────────────────────────────────────────

@app.get("/progress", dependencies=_auth)
def progress():
    """Knowledge progress snapshot: mastered, in-progress, not-started topics, coverage stats."""
    from datetime import date, timedelta
    import json

    today = date.today().isoformat()
    srs_data = _load_srs_data()
    topics_map = srs_data.get("topics", {})

    mastered: list[dict] = []
    in_progress: list[dict] = []
    not_started: list[dict] = []

    total_study_mins_week = 0
    week_ago = (date.today() - timedelta(days=7)).isoformat()

    categories: dict[str, int] = {}

    for key, t in topics_map.items():
        name = t.get("display_name", key)
        reps = t.get("repetitions", 0)
        ef = t.get("ef", 2.5)
        last = t.get("last_reviewed", "")
        history = t.get("history", [])
        mastery = min(100, round((reps * 12) * (ef / 2.5)))

        # Categorise by first word of key as a rough category
        _cat = key.split()[0].title() if key.split() else "Other"
        categories[_cat] = categories.get(_cat, 0) + 1

        # Estimate weekly study minutes: 10 min per session in last 7 days
        recent_sessions = [h for h in history if h.get("date", "") >= week_ago]
        total_study_mins_week += len(recent_sessions) * 10

        entry = {
            "topic": name,
            "mastery": mastery,
            "repetitions": reps,
            "ef": round(ef, 2),
            "last_reviewed": last,
        }

        if reps == 0 and not last:
            not_started.append(entry)
        elif mastery >= 70:
            mastered.append(entry)
        else:
            in_progress.append(entry)

    mastered.sort(key=lambda x: -x["mastery"])
    in_progress.sort(key=lambda x: -x["mastery"])

    total = len(topics_map)
    coverage_pct = round(100 * len(mastered) / total) if total > 0 else 0

    # Knowledge coverage by category
    coverage_by_category: list[dict] = [
        {"category": cat, "topic_count": cnt}
        for cat, cnt in sorted(categories.items(), key=lambda x: -x[1])
    ]

    return {
        "mastered": mastered[:20],
        "in_progress": in_progress[:20],
        "not_started": not_started[:20],
        "counts": {
            "mastered": len(mastered),
            "in_progress": len(in_progress),
            "not_started": len(not_started),
            "total": total,
        },
        "weekly_study_minutes_estimate": total_study_mins_week,
        "knowledge_coverage_pct": coverage_pct,
        "coverage_by_category": coverage_by_category,
        "date": today,
    }


# ── DAILY INSIGHTS ────────────────────────────────────────────────────────────

@app.get("/daily/insights", dependencies=_auth)
def daily_insights():
    """Quick insights: last 7 days of activity, most active topics, SRS stats, recommended focus."""
    from datetime import date, timedelta
    import json

    today = date.today()
    today_iso = today.isoformat()
    week_ago = (today - timedelta(days=7)).isoformat()

    srs_data = _load_srs_data()
    topics_map = srs_data.get("topics", {})

    # ── Recent ingested items ─────────────────────────────────────────────────
    recent_ingested: list[dict] = []
    try:
        store = get_store()
        _res = store.collection.get(include=["metadatas", "documents"], limit=500)
        _seen_titles: set = set()
        for _meta in (_res.get("metadatas") or []):
            _ingested_at = _meta.get("ingested_at") or _meta.get("created_at") or ""
            _title = _meta.get("title", "")
            if _ingested_at >= week_ago and _title and _title not in _seen_titles:
                _seen_titles.add(_title)
                recent_ingested.append({
                    "title": _title,
                    "source": _meta.get("source", ""),
                    "date": _ingested_at[:10] if _ingested_at else "",
                })
        recent_ingested.sort(key=lambda x: x["date"], reverse=True)
    except Exception:
        pass

    # ── Most active topics (by session count in last 7 days) ─────────────────
    topic_activity: list[dict] = []
    total_sessions_week = 0
    total_correct = 0
    total_reviewed = 0

    for key, t in topics_map.items():
        name = t.get("display_name", key)
        history = t.get("history", [])
        recent = [h for h in history if h.get("date", "") >= week_ago]
        if recent:
            sessions = len(recent)
            total_sessions_week += sessions
            correct = sum(1 for h in recent if h.get("score") == "correct")
            total_correct += correct
            total_reviewed += sessions
            topic_activity.append({
                "topic": name,
                "sessions_this_week": sessions,
                "correct_rate": round(correct / sessions, 2) if sessions else 0,
            })

    topic_activity.sort(key=lambda x: -x["sessions_this_week"])

    # ── SRS performance stats ─────────────────────────────────────────────────
    due_count = sum(
        1 for t in topics_map.values()
        if t.get("next_review") and t["next_review"] <= today_iso
    )
    overall_correct_rate = round(total_correct / total_reviewed, 2) if total_reviewed > 0 else 0.0

    # ── Recommended focus (top 3 from /recommendations logic) ────────────────
    focus_areas: list[str] = []
    for key, t in topics_map.items():
        nr = t.get("next_review", "")
        if nr and nr <= today_iso:
            focus_areas.append(t.get("display_name", key))
        if len(focus_areas) >= 3:
            break

    if not focus_areas:
        # Fall back to stale topics
        stale = sorted(
            [(t.get("last_reviewed", ""), t.get("display_name", k)) for k, t in topics_map.items() if t.get("last_reviewed")],
        )
        focus_areas = [name for _, name in stale[:3]]

    return {
        "date": today_iso,
        "last_7_days": {
            "ingested_items": recent_ingested[:10],
            "ingested_count": len(recent_ingested),
            "study_sessions": total_sessions_week,
        },
        "most_active_topics": topic_activity[:5],
        "srs_performance": {
            "due_today": due_count,
            "sessions_this_week": total_sessions_week,
            "overall_correct_rate": overall_correct_rate,
        },
        "recommended_focus": focus_areas,
    }


# ── DAILY ─────────────────────────────────────────────────────────────────────

@app.get("/daily", dependencies=_auth)
async def daily(refresh: bool = False):
    """Daily fun fact + vocab word — cached until midnight. Falls back to last cached version on LLM failure."""
    import json
    from pathlib import Path
    from datetime import date

    cache_path = Path.home() / ".neuron" / "daily_cache.json"
    today = date.today().isoformat()

    if not refresh:
        # 1. Check in-memory cache first (fastest)
        cached_mem = _mc_get("daily")
        if cached_mem and cached_mem.get("date") == today:
            _record_cache_hit("daily")
            return JSONResponse(content=cached_mem, headers={"Cache-Control": "max-age=3600, private", "X-Cache": "MEM"})
        # 2. Fall back to file cache
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                if cached.get("date") == today:
                    _mc_set("daily", cached, _TTL_DAILY)
                    _record_cache_hit("daily")
                    return JSONResponse(content=cached, headers={"Cache-Control": "max-age=3600, private", "X-Cache": "FILE"})
            except Exception:
                pass

    if refresh:
        _mc_delete("daily")
        _record_cache_miss("daily")

    try:
        loop = asyncio.get_event_loop()
        engine = get_engine()
        t0 = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, engine.daily),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[TIMEOUT] /daily LLM call timed out after 60s")
            # Fall back to any previous cached version (even if stale/different date)
            if cache_path.exists():
                try:
                    stale = json.loads(cache_path.read_text())
                    stale["_stale"] = True
                    stale["_fallback_reason"] = "LLM timeout"
                    return JSONResponse(content=stale, headers={"Cache-Control": "no-cache", "X-Cache": "STALE"})
                except Exception:
                    pass
            return JSONResponse(
                status_code=503,
                content={"error": "Daily generation timed out and no cached version available."},
            )
        logger.info("[TIMING] /daily LLM call: %.1fs", time.perf_counter() - t0)
        result["date"] = today
        _mc_set("daily", result, _TTL_DAILY)
        _record_cache_hit("daily")
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return JSONResponse(content=result, headers={"Cache-Control": "max-age=3600, private"})
    except Exception as e:
        logger.error("[DAILY] LLM call failed: %s", e)
        # Fallback: return last cached version (even if stale)
        if cache_path.exists():
            try:
                stale = json.loads(cache_path.read_text())
                stale["_stale"] = True
                stale["_fallback_reason"] = str(e)
                return JSONResponse(content=stale, headers={"Cache-Control": "no-cache", "X-Cache": "STALE"})
            except Exception:
                pass
        raise HTTPException(status_code=503, detail=f"Daily failed and no cached version available: {e}")
