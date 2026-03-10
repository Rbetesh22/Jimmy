"""
Neuron API server.
Run with: neuron serve
Local:  http://localhost:7700
Cloud:  deploy this behind nginx/Railway/Fly.io
"""
import io
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..ingestion.base import Document
from ..storage.store import NeuronStore
from ..retrieval.engine import NeuronEngine
from ..config import CHROMA_DIR

app = FastAPI(title="Neuron", version="0.1.0")

# Allow browser extension and local web UI to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

UI_DIR = Path(__file__).parent.parent / "ui"


def _purge_stale_caches() -> None:
    """Delete stale AI cache files on startup. recs_cache > 24h, study_plan_cache > 2h."""
    import json as _json
    from datetime import datetime as _dt, timedelta as _td

    STALE_RULES = {
        "recs_cache.json": _td(hours=24),
        "study_plan_cache.json": _td(hours=2),
    }
    neuron_dir = Path.home() / ".neuron"
    for fname, max_age in STALE_RULES.items():
        p = neuron_dir / fname
        if not p.exists():
            continue
        try:
            data = _json.loads(p.read_text())
            cached_at = _dt.fromisoformat(data.get("cached_at", "2000-01-01"))
            if _dt.now() - cached_at > max_age:
                p.unlink(missing_ok=True)
        except Exception:
            pass


# Purge stale caches at import time (i.e., server startup)
_purge_stale_caches()


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


def _chunk_and_store(docs: list[Document], store: NeuronStore):
    from ..cli import chunk_text
    chunks, metadatas, ids = [], [], []
    seen: set[str] = set()
    for doc in docs:
        prefix = f"[{doc.source.upper()}: {doc.title}]\n\n"
        for i, chunk in enumerate(chunk_text(doc.content)):
            cid = f"{doc.id}_c{i}"
            if cid not in seen:
                seen.add(cid)
                chunks.append(prefix + chunk)
                metadatas.append({**doc.metadata, "title": doc.title, "source": doc.source})
                ids.append(cid)
    if chunks:
        store.upsert(chunks, metadatas, ids)
    return len(chunks), len(docs)


# ── STATUS ─────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"name": "Neuron", "version": "0.1.0", "status": "running"}


@app.get("/status")
def status():
    store = get_store()
    total = store.count()
    # Fetch only metadatas (no documents/embeddings) — fast even for 130k+ docs
    breakdown: dict[str, int] = {}
    try:
        result = store.collection.get(include=["metadatas"])
        for meta in result["metadatas"]:
            src = meta.get("source", "")
            if src:
                breakdown[src] = breakdown.get(src, 0) + 1
    except Exception:
        pass
    return {"total_chunks": total, "sources": breakdown}


# ── INGEST ─────────────────────────────────────────────────────────────────────

class IngestURLRequest(BaseModel):
    url: str


class IngestTextRequest(BaseModel):
    text: str
    title: str | None = None
    source: str = "note"


@app.post("/ingest/url")
def ingest_url(req: IngestURLRequest):
    """Ingest a web page — called by the browser extension."""
    from ..ingestion.web import WebIngester
    try:
        docs = WebIngester().ingest(req.url)
        store = get_store()
        chunks, n_docs = _chunk_and_store(docs, store)
        return {"ok": True, "chunks": chunks, "documents": n_docs, "title": docs[0].title if docs else req.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/ingest/text")
def ingest_text(req: IngestTextRequest):
    """Ingest a note, idea, or any raw text."""
    import uuid
    from datetime import datetime
    doc = Document(
        id=f"{req.source}_{uuid.uuid4().hex[:8]}",
        content=req.text,
        source=req.source,
        title=req.title or f"Note — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        metadata={"type": req.source, "created_at": datetime.now().isoformat()},
    )
    store = get_store()
    chunks, n_docs = _chunk_and_store([doc], store)
    return {"ok": True, "chunks": chunks, "documents": n_docs}


@app.post("/ingest/file")
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


@app.post("/ingest/goodnotes")
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


@app.post("/ingest/youtube")
def ingest_youtube(req: IngestURLRequest):
    from ..ingestion.youtube import YouTubeIngester
    try:
        docs = YouTubeIngester().ingest(req.url)
        store = get_store()
        chunks, n_docs = _chunk_and_store(docs, store)
        return {"ok": True, "chunks": chunks, "documents": n_docs, "title": docs[0].title if docs else req.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── RETRIEVAL ──────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    q: str
    n_results: int = 25


@app.post("/ask")
def ask(req: QueryRequest):
    try:
        return get_engine().ask(req.q, n_results=req.n_results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ask/quick")
def ask_quick(req: QueryRequest):
    """Quick inline answer — small context, fast response, max 15s timeout. Returns {answer: str, sources: list}."""
    import os as _os
    import signal as _signal
    from ..retrieval.engine import _build_numbered_context
    from datetime import datetime as _dt

    engine = get_engine()

    try:
        # Use fewer results for speed
        queries = engine._expand_query(req.q) if len(req.q.split()) > 3 else [req.q]
        scored = engine._multi_search(queries, n_candidates=60)

        # Deduplicate by title
        seen_keys: set = set()
        deduped = []
        for item in scored:
            meta = item[2]
            key = f"{meta.get('source', '')}::{meta.get('title', '')}"
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(item)

        scored = deduped[:8]  # small context for speed

        if not scored:
            return {"answer": "Nothing relevant found in your knowledge base.", "sources": []}

        docs = [x[1] for x in scored]
        metas = [x[2] for x in scored]
        context, sources = _build_numbered_context(docs, metas)

        today = _dt.now().strftime("%A, %B %d, %Y")
        prompt = (
            f"You are Neuron — a second brain. Today is {today}.\n"
            f"Answer the question concisely (2-4 sentences max) using ONLY what is in the sources below.\n"
            f"Cite inline [N]. If sources don't cover it, say so briefly.\n\n"
            f"SOURCES:\n{context}\n\n"
            f"QUESTION: {req.q}"
        )

        answer = engine._chat(prompt, max_tokens=400, model="claude-haiku-4-5-20251001")
        return {"answer": answer.strip(), "sources": sources}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ask/stream")
def ask_stream(req: QueryRequest):
    """Streaming version of /ask — returns SSE with token-by-token answer."""
    import json as _json
    import os as _os

    engine = get_engine()

    def generate():
        try:
            # Run retrieval (fast)
            from ..retrieval.engine import _build_numbered_context
            from datetime import datetime
            _now = datetime.now()
            today = _now.strftime("%A, %B ") + str(_now.day) + _now.strftime(", %Y")

            queries = engine._expand_query(req.q)
            scored = engine._multi_search(queries, n_candidates=200)

            seen_title_keys: set[str] = set()
            deduped = []
            for item in scored:
                meta = item[2]
                key = f"{meta.get('source', '')}::{meta.get('title', '')}"
                if key not in seen_title_keys:
                    seen_title_keys.add(key)
                    deduped.append(item)

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

            prompt = (
                f"You are Neuron — a second brain built from this person's actual notes, meetings, courses, and work.\n"
                f"Today is {today}.{upcoming_section}\n\n"
                f"KNOWLEDGE CALIBRATION (critical — read carefully):\n"
                f"Each source is tagged with what the person likely knows:\n"
                f"- ⚠ NOT YET COVERED IN COURSE: This is in their curriculum but hasn't been taught yet. Do NOT assume they know it. You can mention it exists but flag it clearly.\n"
                f"- ⚠ ACTIVELY STUDYING: Current coursework — they're learning it now, may have gaps.\n"
                f"- ⚠ BUILT THIS (hands-on mastery): They built or coded this themselves. Deep familiarity — you can go technical.\n"
                f"- ⚠ PERSONAL NOTE: Their own thinking and synthesis. Treat as their own understanding.\n"
                f"- ⚠ STUDIED PREVIOUSLY (may have faded): They learned this but may not remember details.\n"
                f"- ⚠ OLDER MATERIAL (likely faded): Old content — jog their memory, don't assume fluency.\n\n"
                f"STRICT RULES:\n"
                f"- Answer ONLY from what is explicitly stated in the sources. Do not infer or fill gaps.\n"
                f"- If the sources don't contain enough to answer, say exactly that.\n"
                f"- Cite every claim inline like [1][2].\n"
                f"- Name specific people, projects, dates, and decisions from the sources.\n"
                f"- Sources marked UNREAD/SAVED: saved but NOT yet consumed — say 'you've saved' not 'you read'.\n"
                f"- Do not pad the answer — stop when the sources run out of relevant information.\n"
                f"- NEVER infer habits, routines, or frequency from individual data points.\n\n"
                f"SOURCES:\n{context}\n\n"
                f"QUESTION: {req.q}"
            )

            anthropic_key = _os.environ.get("ANTHROPIC_API_KEY", "").strip()
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
                yield f"data: {_json.dumps({'type': 'done', 'answer': full_text, 'sources': sources})}\n\n"
            else:
                # Non-streaming fallback
                answer = engine._chat(prompt, max_tokens=4000)
                yield f"data: {_json.dumps({'type': 'done', 'answer': answer, 'sources': sources})}\n\n"

        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'detail': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/context")
def context_pack(req: QueryRequest):
    try:
        return get_engine().context_pack(req.q, n_results=req.n_results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/resurface")
def resurface(req: QueryRequest):
    try:
        return get_engine().resurface(req.q, n_results=req.n_results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/resurface/random")
def resurface_random():
    """Return a random interesting chunk from the KB, prioritizing apple_notes, granola, canvas sources."""
    import random as _random
    store = get_store()

    # Prioritized sources — personal and course content
    PRIORITY_SOURCES = ["apple_notes", "granola", "canvas", "note", "notion", "kindle", "readwise"]
    EXCLUDE_SOURCES = {"calendar"}

    try:
        # Try to get chunks from priority sources first via targeted searches
        SEARCH_SEEDS = [
            "insight idea concept note",
            "learned realized discovered thought",
            "lecture class course assignment",
            "meeting discussion talked decided",
            "highlight quote passage book reading",
        ]
        seen_ids: set = set()
        candidates = []

        for seed in SEARCH_SEEDS:
            try:
                res = store.search(seed, n_results=20)
                for doc, meta, doc_id in zip(res["documents"][0], res["metadatas"][0], res["ids"][0]):
                    if doc_id in seen_ids:
                        continue
                    src = meta.get("source", "")
                    if src in EXCLUDE_SOURCES:
                        continue
                    seen_ids.add(doc_id)
                    priority = 0 if src in PRIORITY_SOURCES else 1
                    candidates.append((priority, doc, meta, doc_id))
            except Exception:
                continue

        if not candidates:
            raise HTTPException(status_code=404, detail="No content found in knowledge base.")

        # Sort by priority, then pick randomly within the top priority tier
        candidates.sort(key=lambda x: x[0])
        top_priority = candidates[0][0]
        top_pool = [c for c in candidates if c[0] == top_priority]
        chosen = _random.choice(top_pool)
        _, doc, meta, doc_id = chosen

        return {
            "id": doc_id,
            "title": meta.get("title", ""),
            "source": meta.get("source", ""),
            "content": doc[:600] + ("..." if len(doc) > 600 else ""),
            "date": meta.get("date", meta.get("created_at", "")),
            "url": meta.get("url", ""),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/connections")
def connections(req: QueryRequest):
    try:
        return get_engine().connections(req.q, n_results=15)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/library/connections/{title:path}")
def library_connections(title: str):
    """Search the KB for content related to a book/resource title and return connections."""
    try:
        engine = get_engine()
        # Use the connections engine method — it already does multi-search + LLM synthesis
        result = engine.connections(title, n_results=20)
        # Also search for direct title matches to surface exact highlights
        store = get_store()
        direct_hits = []
        try:
            res = store.search(title, n_results=10)
            seen_titles: set = set()
            for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
                t = meta.get("title", "")
                if t and t.lower() != title.lower() and t not in seen_titles:
                    seen_titles.add(t)
                elif not t:
                    continue
                if meta.get("title", "").lower() == title.lower() or title.lower() in meta.get("title", "").lower():
                    direct_hits.append({
                        "title": meta.get("title", ""),
                        "source": meta.get("source", ""),
                        "snippet": doc[:300],
                        "url": meta.get("url", ""),
                        "date": meta.get("date", ""),
                    })
        except Exception:
            pass

        result["title"] = title
        result["direct_hits"] = direct_hits[:5]
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/digest")
def digest(refresh: bool = False):
    """Daily digest — cached 60 min. Pass ?refresh=true to regenerate."""
    import json
    from pathlib import Path
    from datetime import datetime, timedelta

    cache_path = Path.home() / ".neuron" / "digest_cache.json"

    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(minutes=60):
                return cached
        except Exception:
            pass

    try:
        result = get_engine().digest()
        result["cached_at"] = datetime.now().isoformat()
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/today")
def today_combined(refresh: bool = False):
    """Combined daily endpoint: digest summary + daily extras + countdowns. Cached 60 min."""
    import json
    from pathlib import Path
    from datetime import datetime, timedelta, date as _date

    cache_path = Path.home() / ".neuron" / "today_cache.json"

    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(minutes=60):
                return cached
        except Exception:
            pass

    result: dict = {}

    # Countdowns
    datadog_start = _date(2026, 8, 31)
    graduation = _date(2026, 5, 15)
    today = _date.today()
    result["countdowns"] = {
        "datadog_days": (datadog_start - today).days,
        "graduation_days": max(0, (graduation - today).days),
    }

    # Pull in daily extras (fact, vocab, motivational_note) — use cache if fresh
    try:
        daily_cache = Path.home() / ".neuron" / "daily_cache.json"
        if daily_cache.exists():
            daily_data = json.loads(daily_cache.read_text())
            daily_cached_at = datetime.fromisoformat(daily_data.get("cached_at", "2000-01-01"))
            if datetime.now() - daily_cached_at < timedelta(hours=24):
                result["fact"] = daily_data.get("fact")
                result["vocab"] = daily_data.get("vocab")
                result["motivational_note"] = daily_data.get("motivational_note")
                result["parasha"] = daily_data.get("parasha", "")
            else:
                raise ValueError("daily cache stale")
        else:
            raise ValueError("no daily cache")
    except Exception:
        try:
            daily_result = get_engine().daily_extras()
            result["fact"] = daily_result.get("fact")
            result["vocab"] = daily_result.get("vocab")
            result["motivational_note"] = daily_result.get("motivational_note")
            # Fetch parasha
            parasha_text = ""
            try:
                import httpx as _httpx
                r = _httpx.get("https://www.sefaria.org/api/calendars", timeout=5)
                if r.status_code == 200:
                    cal = r.json()
                    for item in cal.get("calendar_items", []):
                        if item.get("title", {}).get("en", "") == "Parashat Hashavua":
                            parasha_text = item.get("displayValue", {}).get("en", "")
                            break
            except Exception:
                pass
            result["parasha"] = parasha_text
        except Exception:
            result["fact"] = None
            result["vocab"] = None
            result["motivational_note"] = None
            result["parasha"] = ""

    result["cached_at"] = datetime.now().isoformat()
    cache_path.parent.mkdir(exist_ok=True)
    try:
        cache_path.write_text(json.dumps(result))
    except Exception:
        pass
    return result


@app.get("/daily")
def daily(refresh: bool = False):
    """Daily fun fact + vocab word personalized from the KB. Cached 24 hours."""
    import json
    from pathlib import Path
    from datetime import datetime, timedelta

    cache_path = Path.home() / ".neuron" / "daily_cache.json"

    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(hours=24):
                return cached
        except Exception:
            pass

    try:
        result = get_engine().daily_extras()

        # Fetch weekly Torah portion from Sefaria API
        parasha_text = ""
        try:
            import httpx as _httpx
            r = _httpx.get("https://www.sefaria.org/api/calendars", timeout=5)
            if r.status_code == 200:
                cal = r.json()
                for item in cal.get("calendar_items", []):
                    if item.get("title", {}).get("en", "") == "Parashat Hashavua":
                        parasha_text = item.get("displayValue", {}).get("en", "")
                        break
        except Exception:
            pass
        result["parasha"] = parasha_text

        result["cached_at"] = datetime.now().isoformat()
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CanvasConfigRequest(BaseModel):
    token: str
    base_url: str = ""

class ReadwiseConfigRequest(BaseModel):
    token: str

@app.post("/config/canvas")
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

@app.post("/config/readwise")
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

@app.get("/auth/google")
def auth_google():
    """Return Google OAuth URL for onboarding."""
    try:
        from ..ingestion.google_auth import get_auth_url
        return {"auth_url": get_auth_url()}
    except Exception as e:
        return {"auth_url": None, "message": str(e)}


@app.post("/refresh")
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
                  "recs_cache.json", "sparks_cache.json", "suggestions_cache.json"):
        try:
            (_Path.home() / ".neuron" / fname).unlink(missing_ok=True)
        except Exception:
            pass
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


@app.get("/upcoming")
def upcoming(days: int = 14):
    """What's on your calendar in the next N days?"""
    try:
        return get_engine().upcoming(days=days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/recent")
def recent(days: int = 14):
    """What have you been taking in lately? Temporal browse by date."""
    try:
        return get_engine().recent(days=days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/graph-ui", response_class=HTMLResponse)
def graph_ui():
    return (UI_DIR / "graph.html").read_text()


@app.get("/graph")
def graph_data():
    """Return cached topic graph, or signal that it needs to be built."""
    import json
    from pathlib import Path
    cache_path = Path.home() / ".neuron" / "graph_cache.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    return {"nodes": [], "edges": [], "needs_build": True}


@app.post("/graph/build")
def graph_build():
    """Analyze KB with Claude and build topic graph. Takes ~15s."""
    try:
        return get_engine().build_topic_graph()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class NodeRequest(BaseModel):
    label: str
    category: str = ""


@app.post("/node/summary")
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


class EvaluateRequest(BaseModel):
    question: str
    user_answer: str
    correct_answer: str
    explanation: str
    topic: str


@app.post("/practice")
def practice(req: PracticeRequest):
    """Generate practice exercises on a topic from the user's knowledge base."""
    try:
        return get_engine().practice(req.topic)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/practice/evaluate")
def evaluate_answer(req: EvaluateRequest):
    """Evaluate a user's practice answer with AI feedback."""
    try:
        return get_engine().evaluate_answer(
            req.question, req.user_answer, req.correct_answer, req.explanation, req.topic
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/spark")
def spark(days_recent: int = 14, days_old: int = 60, refresh: bool = False):
    """Find unexpected connections between recent and older knowledge. Cached for 6h."""
    import json
    from pathlib import Path
    from datetime import datetime, timedelta

    cache_path = Path.home() / ".neuron" / "sparks_cache.json"

    def _migrate_sparks(data: dict) -> dict:
        """Ensure all sparks have why_it_matters; fall back to connection field if missing."""
        for spark_item in data.get("sparks", []):
            if not spark_item.get("why_it_matters"):
                spark_item["why_it_matters"] = spark_item.get("connection", "")
        return data

    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(hours=6):
                return _migrate_sparks(cached)
        except Exception:
            pass

    try:
        result = get_engine().spark(days_recent=days_recent, days_old=days_old)
        result = _migrate_sparks(result)
        result["cached_at"] = datetime.now().isoformat()
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/timeline")
def timeline(weeks: int = 16):
    """Learning activity grouped by week for timeline visualization. Cached 15 min."""
    import json
    from pathlib import Path
    from datetime import datetime, timedelta

    cache_path = Path.home() / ".neuron" / "timeline_cache.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(minutes=15):
                return cached
        except Exception:
            pass

    try:
        result = get_engine().timeline(weeks=weeks)
        result["cached_at"] = datetime.now().isoformat()
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/suggestions")
def suggestions():
    """Return 4 personalized question suggestions based on recent KB content."""
    import json, re
    from pathlib import Path
    from datetime import datetime, timedelta

    cache_path = Path.home() / ".neuron" / "suggestions_cache.json"
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
        store = get_store()
        if store.count() == 0:
            return {"suggestions": []}

        # Fast sampling via targeted searches across themes — avoids full collection scan
        import random
        EXCLUDE = {"calendar"}
        SEARCH_SEEDS = [
            "lecture notes exam concept theorem",
            "email meeting project update",
            "book highlights reading insight",
            "personal notes thoughts journal",
            "career work internship job",
            "code programming algorithm implementation",
            "finance money investment accounting",
            "history philosophy religion culture",
        ]
        seen_titles: set[str] = set()
        sample: list = []
        for seed in SEARCH_SEEDS:
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
                    sample.append((doc, meta))
            except Exception:
                continue

        if not sample:
            return {"suggestions": []}

        random.shuffle(sample)
        sample = sample[:30]

        ctx = "\n\n".join(
            f"[{m.get('source','')}] {m.get('title','')}: {d[:180]}"
            for d, m in sample
        )

        raw = engine._chat(
            "You are generating personalized question suggestions for someone's second-brain app. "
            "Based on these knowledge items from DIFFERENT areas of their life (courses, meetings, notes, emails, reading), "
            "generate exactly 4 short, specific, genuinely curious questions they might want to ask.\n"
            "IMPORTANT: Make the questions diverse — span different topics/sources, not all from one subject. "
            "Each question should feel personal and interesting, not generic.\n"
            "Return ONLY a JSON array of 4 strings, no markdown, no explanation.\n\n"
            f"KNOWLEDGE ITEMS:\n{ctx}",
            max_tokens=350,
            model="claude-haiku-4-5-20251001",
        )
        m = re.search(r'\[[\s\S]*?\]', raw)
        suggestions_list = []
        if m:
            try:
                suggestions_list = [s for s in json.loads(m.group(0)) if isinstance(s, str)][:4]
            except Exception:
                pass

        result = {"suggestions": suggestions_list, "cached_at": datetime.now().isoformat()}
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return result
    except Exception as e:
        return {"suggestions": []}


@app.get("/search")
def search(q: str, n: int = 8):
    """Raw semantic search — returns chunks with composite scores."""
    from ..retrieval.engine import _rerank_scored
    store = get_store()
    results = store.search(q, n_results=n * 2)
    distances = results["distances"][0]
    scored = _rerank_scored(
        results["documents"][0],
        results["metadatas"][0],
        results["ids"][0],
        distances,
    )
    items = []
    seen_titles: set[str] = set()
    for composite, doc, meta, doc_id in scored:
        title = meta.get("title", "")
        src = meta.get("source", "")
        key = f"{src}::{title}"
        if key in seen_titles:
            continue
        seen_titles.add(key)
        items.append({
            "content": doc[:300] + "..." if len(doc) > 300 else doc,
            "title": title,
            "source": src,
            "composite_score": round(composite, 3),
        })
        if len(items) >= n:
            break
    return {"results": items, "query": q}


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


@app.get("/news")
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
            if datetime.now() - cached_at < timedelta(minutes=30):
                return cached
        except Exception:
            pass

    RSS_FEEDS = [
        # World / Breaking
        {"url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "category": "World", "label": "NY Times"},
        {"url": "https://feeds.bbci.co.uk/news/world/rss.xml", "category": "World", "label": "BBC World"},
        {"url": "https://feeds.reuters.com/reuters/topNews", "category": "World", "label": "Reuters"},
        {"url": "https://www.aljazeera.com/xml/rss/all.xml", "category": "World", "label": "Al Jazeera"},
        # Israel / Middle East
        {"url": "https://www.timesofisrael.com/feed/", "category": "Israel", "label": "Times of Israel"},
        {"url": "https://www.jta.org/feed", "category": "Israel", "label": "JTA"},
        {"url": "https://www.israelnationalnews.com/Rss.aspx", "category": "Israel", "label": "Arutz Sheva"},
        {"url": "https://www.jpost.com/rss/rssfeedsfrontpage.aspx", "category": "Israel", "label": "Jerusalem Post"},
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
        # Finance / Business
        {"url": "https://feeds.bloomberg.com/markets/news.rss", "category": "Finance", "label": "Bloomberg"},
        {"url": "https://www.wsj.com/xml/rss/3_7085.xml", "category": "Finance", "label": "WSJ Markets"},
        # Sports
        {"url": "https://www.espn.com/espn/rss/news", "category": "Sports", "label": "ESPN"},
        {"url": "https://www.espn.com/espn/rss/nba/news", "category": "Sports", "label": "ESPN NBA"},
        {"url": "https://www.espn.com/espn/rss/nfl/news", "category": "Sports", "label": "ESPN NFL"},
        {"url": "https://feeds.bbci.co.uk/sport/rss.xml", "category": "Sports", "label": "BBC Sport"},
    ]

    articles = []

    def _parse_rss(feed_info: dict, xml_text: str) -> list[dict]:
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
                desc_el = item.find("description") or item.find("atom:summary", ns) or item.find("atom:content", ns)
                desc = ""
                if desc_el is not None and desc_el.text:
                    desc = re.sub(r"<[^>]+>", " ", desc_el.text).strip()[:200]
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
                        item.find("{http://purl.org/rss/1.0/modules/content/}encoded"),
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

                if title and link:
                    items.append({
                        "title": title.strip(),
                        "url": link.strip(),
                        "description": desc,
                        "image": image,
                        "category": feed_info["category"],
                        "source": feed_info["label"],
                    })
        except Exception:
            pass
        return items

    from concurrent.futures import ThreadPoolExecutor, as_completed

    UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"

    def _fetch_feed(feed: dict) -> list[dict]:
        try:
            with httpx.Client(timeout=6, follow_redirects=True) as c:
                resp = c.get(feed["url"], headers={"User-Agent": UA})
                if resp.status_code == 200:
                    return _parse_rss(feed, resp.text)
        except Exception:
            pass
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

    # Deduplicate by title (normalize: lowercase, strip punctuation)
    seen_titles: set[str] = set()
    deduped: list[dict] = []
    for a in articles:
        key = re.sub(r"[^a-z0-9]", "", a["title"].lower())[:60]
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append(a)
    articles = deduped

    # Prefer articles with images, but keep all
    articles.sort(key=lambda a: (0 if a.get("image") else 1))

    # Group by category (cap 10 per category)
    by_category: dict[str, list] = {}
    cat_counts: dict[str, int] = {}
    for a in articles:
        c = a["category"]
        if cat_counts.get(c, 0) < 10:
            by_category.setdefault(c, []).append(a)
            cat_counts[c] = cat_counts.get(c, 0) + 1

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


@app.get("/news/summary")
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
            if datetime.now() - cached_at < timedelta(minutes=30):
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

        # Build headline context
        ctx_parts = []
        for cat, arts in by_cat.items():
            ctx_parts.append(f"{cat.upper()}:")
            for a in arts[:4]:
                ctx_parts.append(f"  - {a['title']} ({a['source']})")
        ctx = "\n".join(ctx_parts)

        engine = get_engine()
        summary_text = engine._chat(
            f"You are writing a personal morning briefing for Ralph — a Columbia University student intensely interested in "
            f"Israel/Middle East (especially current events, IDF, Hamas, geopolitics), Torah/Jewish life (parasha, halacha, Rabbi Avi Harari), "
            f"AI/startups (OpenAI, Anthropic, LLMs), Columbia University, US politics, and finance. Today is {today}.\n\n"
            f"Today's headlines by category:\n{ctx}\n\n"
            f"Write a rich morning briefing with 4 sections (use markdown ## headers). Be substantive — this is the main briefing, not a teaser.\n\n"
            f"## What's Happening\n"
            f"Lead with Israel/Middle East if anything is there — give 3-4 sentences covering the key development, who's involved, what it means. "
            f"If no Israel story, lead with the biggest world or political story. Be specific: names, places, numbers.\n\n"
            f"## The World Today\n"
            f"3-4 bullets. Cover US politics, global events, and anything from the Torah/Jewish category (parasha of the week, a shiur, a Jewish community story). "
            f"Each bullet is 1-2 sentences. Prioritize what Ralph would actually care about.\n\n"
            f"## Markets & Tech\n"
            f"2-3 bullets on finance/markets and AI/tech. What moved, who launched what, what's the signal. Be concrete — numbers, names, companies.\n\n"
            f"## In the Game\n"
            f"If sports stories exist, 1-2 sentences on the key result or storyline. If nothing notable, skip.\n\n"
            f"Rules: Minimum 300 words. Be direct and specific. Write like a smart friend who reads everything, not a press release.",
            max_tokens=800,
            model="claude-haiku-4-5-20251001",
        )

        result = {"summary": summary_text, "cached_at": datetime.now().isoformat()}
        summary_cache_path.parent.mkdir(exist_ok=True)
        try:
            summary_cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return result
    except Exception as e:
        return {"summary": ""}


@app.get("/datadog-prep")
def datadog_prep():
    """Generate Datadog-specific study plan for query engine prep (Arrow, Trino, ClickHouse, Calcite)."""
    from datetime import date as _date

    today = _date.today()
    datadog_start = _date(2026, 8, 31)
    days_until_start = (datadog_start - today).days

    TOPICS = [
        {
            "name": "Apache Arrow",
            "description": "Columnar in-memory data format and inter-process communication standard. Core to Datadog's query pipeline.",
            "key_concepts": [
                "Columnar memory layout vs row-based",
                "Zero-copy reads and memory-mapped files",
                "Arrow IPC format (stream and file)",
                "Arrow Flight (high-speed data transfer over gRPC)",
                "PyArrow, Java Arrow, Go Arrow bindings",
                "Arrow compute kernels",
                "Dictionary encoding and run-length encoding",
                "Nested types: lists, structs, maps, unions",
            ],
            "resources": [
                "Apache Arrow official docs: https://arrow.apache.org/docs/",
                "Book: 'In-Memory Analytics with Apache Arrow' by Matthew Topol",
                "Arrow columnar format spec: https://arrow.apache.org/docs/format/Columnar.html",
                "Arrow Flight RPC: https://arrow.apache.org/docs/format/Flight.html",
                "GitHub: apache/arrow",
            ],
        },
        {
            "name": "Trino (formerly PrestoSQL)",
            "description": "Distributed SQL query engine for analytics at scale. Used for federated queries across many data sources.",
            "key_concepts": [
                "Coordinator-worker architecture",
                "SPI (Service Provider Interface) — connectors",
                "Stage-based query execution and pipelining",
                "Cost-based optimizer (CBO)",
                "Dynamic filtering",
                "Spill-to-disk for memory management",
                "Exchange operators and shuffle",
                "Vectorized evaluation with Arrow",
                "Fault-tolerant execution (FTE)",
            ],
            "resources": [
                "Trino: The Definitive Guide (O'Reilly, free PDF on trino.io)",
                "Trino docs: https://trino.io/docs/current/",
                "Trino blog: https://trino.io/blog/",
                "GitHub: trinodb/trino",
                "Talk: 'Trino at Scale' (Trino Summit recordings on YouTube)",
            ],
        },
        {
            "name": "ClickHouse",
            "description": "Column-oriented OLAP DBMS optimized for real-time analytics. Extremely fast for aggregation queries.",
            "key_concepts": [
                "MergeTree table engine family",
                "Primary key and sparse indexing",
                "Data skipping indexes (minmax, set, bloom filter)",
                "Materialized views and projections",
                "Aggregating merge tree",
                "Vectorized query execution",
                "Compression codecs (LZ4, ZSTD, Delta, Gorilla)",
                "Distributed tables and sharding",
                "ReplicatedMergeTree and Keeper (ZooKeeper)",
                "ClickHouse SQL extensions (ARRAY JOIN, groupArray, etc.)",
            ],
            "resources": [
                "ClickHouse docs: https://clickhouse.com/docs/",
                "ClickHouse University: https://learn.clickhouse.com/",
                "Book: 'ClickHouse in Action' (Manning)",
                "Altinity blog: https://altinity.com/blog/",
                "GitHub: ClickHouse/ClickHouse",
            ],
        },
        {
            "name": "Apache Calcite",
            "description": "SQL parser, validator, and query optimizer framework. The backbone of many query engines including Trino and Flink.",
            "key_concepts": [
                "Relational algebra and relational expressions (RelNode)",
                "Volcano/Cascades optimizer model",
                "Rules: transformation rules vs implementation rules",
                "RelOptPlanner (VolcanoPlanner, HepPlanner)",
                "Cost model and statistics",
                "SQL parsing (SqlParser) and AST",
                "Validation (SqlValidator) and type inference",
                "Adapters and schemas (JDBC, CSV, etc.)",
                "Lattices and materialized views",
            ],
            "resources": [
                "Calcite docs: https://calcite.apache.org/docs/",
                "Paper: 'Apache Calcite: A Foundational Framework for Optimized Query Processing' (SIGMOD 2018)",
                "Tutorial: https://calcite.apache.org/docs/tutorial.html",
                "GitHub: apache/calcite",
                "Talk: 'Building Query Engines with Apache Calcite' (YouTube)",
            ],
        },
    ]

    # Compute days_remaining for each topic — divide total time roughly equally
    days_per_topic = max(1, days_until_start // len(TOPICS)) if days_until_start > 0 else 0
    topics_with_days = []
    for i, topic in enumerate(TOPICS):
        t = dict(topic)
        t["days_remaining_to_study"] = max(0, days_until_start - i * days_per_topic)
        topics_with_days.append(t)

    # Search KB for any related notes on these topics
    engine = get_engine()
    kb_hits = []
    try:
        for seed in ["Apache Arrow columnar query", "Trino Presto distributed SQL", "ClickHouse OLAP analytics", "Calcite query optimizer"]:
            res = engine.store.search(seed, n_results=3)
            for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
                title = meta.get("title", "")
                if title:
                    kb_hits.append({"title": title, "source": meta.get("source", ""), "snippet": doc[:150]})
    except Exception:
        pass

    return {
        "topics": topics_with_days,
        "start_date": "2026-08-31",
        "days_until_start": days_until_start,
        "kb_related": kb_hits,
        "study_tip": (
            f"You have {days_until_start} days until Datadog. "
            "Focus on Arrow + Trino first (most directly relevant to query engineering). "
            "Read the Trino Definitive Guide and Arrow columnar format spec. "
            "Build small projects: write an Arrow IPC reader, run ClickHouse locally on a CSV."
        ),
    }


@app.get("/recommendations")
def recommendations():
    """Generate personalized book and podcast recommendations from KB. Cached 6 hours."""
    import json, re
    from pathlib import Path
    from datetime import datetime, timedelta

    cache_path = Path.home() / ".neuron" / "recs_cache.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < timedelta(hours=6):
                return cached
        except Exception:
            pass

    try:
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
            f"Today is {today}. Ralph is a Columbia CS MS student starting at Datadog (query engineering team) on Aug 31 2026. "
            f"He has {((__import__('datetime').date(2026, 8, 31)) - __import__('datetime').date.today()).days} days until he starts. "
            f"He is preparing for Datadog by learning: Apache Arrow, Trino, ClickHouse, Apache Calcite. "
            f"He is also interested in: Israel/Middle East, Torah/Jewish learning (esp. Rabbi Avi Harari), "
            f"AI/LLMs, entrepreneurship and VC, philosophy, finance, and current events. "
            f"He watches YouTube (tech, AI, finance, Torah lectures, documentary-style content).\n\n"
            f"Based on his current knowledge base below, suggest exactly:\n"
            f"- 2 books he should read next (prioritize query engines, distributed systems, or his Datadog prep if relevant)\n"
            f"- 2 podcast episodes worth listening to (tech, AI, Israel/Jewish world, or entrepreneurship)\n"
            f"- 2 YouTube videos or channels to check out (query engine talks, Torah lectures, startup content)\n\n"
            f"RULES:\n"
            f"- Books: real titles by real authors. Direct connection to what he's studying or preparing for.\n"
            f"- Podcasts: real shows, specific episode if possible. Match Datadog prep OR current interests.\n"
            f"- YouTube: real channels or specific videos (conference talks like Trino Summit, Torah shiurim, startup explainers). Prefer educational/intellectual content.\n"
            f"- Each: 1 sentence WHY it connects to something specific in his KB or Datadog prep.\n"
            f"- No generic picks — be specific and timely.\n\n"
            f"Return ONLY valid JSON:\n"
            f'[{{"type":"book|podcast|youtube","title":"...","author_or_show":"...","why":"1 sentence",'
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

        result = {"recommendations": recs, "cached_at": datetime.now().isoformat()}
        cache_path.parent.mkdir(exist_ok=True)
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return result
    except Exception:
        return {"recommendations": []}
