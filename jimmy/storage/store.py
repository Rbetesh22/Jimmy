import chromadb
from chromadb import Settings
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
import os


def _patch_hnswlib():
    """Patch hnswlib and chromadb for compatibility with old pickle format + hnswlib 0.8.0."""
    try:
        import hnswlib
        if not hasattr(hnswlib.Index, "file_handle_count"):
            hnswlib.Index.file_handle_count = 2
    except ImportError:
        pass

    # Patch PersistentData.load_from_file to handle old dict-format pickle files.
    try:
        from chromadb.segment.impl.vector.local_persistent_hnsw import PersistentData
        import pickle

        @staticmethod  # type: ignore[misc]
        def _patched_load_from_file(filename: str) -> "PersistentData":
            with open(filename, "rb") as f:
                data = pickle.load(f)
            if isinstance(data, dict):
                dim = data.get("dimensionality")
                id_to_label = data.get("id_to_label", {})
                # Old pickles store None for dimensionality but the index exists;
                # infer from model (bge-small-en-v1.5 = 384 dims).
                if dim is None and id_to_label:
                    dim = 384
                return PersistentData(
                    dimensionality=dim,
                    total_elements_added=data.get("total_elements_added", 0),
                    id_to_label=id_to_label,
                    label_to_id={v: k for k, v in id_to_label.items()},
                    id_to_seq_id=data.get("id_to_seq_id", {}),
                )
            # Handle PersistentData objects with None dimensionality
            if hasattr(data, "dimensionality") and data.dimensionality is None and data.id_to_label:
                data.dimensionality = 384
            return data

        PersistentData.load_from_file = _patched_load_from_file
    except Exception:
        pass


def _make_client(data_dir):
    """Use an external Chroma HTTP server when configured, else embedded SegmentAPI."""
    _patch_hnswlib()
    http_host = os.environ.get("CHROMA_HTTP_HOST", "").strip()
    http_port = os.environ.get("CHROMA_HTTP_PORT", "").strip()
    if http_host and http_port:
        # Ensure local chroma connections bypass any system HTTP proxy
        for key in ("NO_PROXY", "no_proxy"):
            existing = os.environ.get(key, "")
            if "127.0.0.1" not in existing:
                os.environ[key] = ",".join(filter(None, [existing, "127.0.0.1", "localhost", http_host]))
        # Prevent httpx from reading macOS system proxy via scutil for local connections
        if "HTTP_PROXY" not in os.environ:
            os.environ["HTTP_PROXY"] = ""
        if "HTTPS_PROXY" not in os.environ:
            os.environ["HTTPS_PROXY"] = ""
        return chromadb.HttpClient(
            host=http_host,
            port=int(http_port),
            settings=Settings(anonymized_telemetry=False),
        )
    s = Settings(
        chroma_api_impl="chromadb.api.segment.SegmentAPI",
        is_persistent=True,
        persist_directory=str(data_dir),
        anonymized_telemetry=False,
    )
    return chromadb.Client(s)


class JimmyStore:
    def __init__(self, data_dir):
        import threading
        from pathlib import Path
        self._data_dir = Path(str(data_dir))
        self.client = _make_client(data_dir)
        self.ef = SentenceTransformerEmbeddingFunction(
            model_name="BAAI/bge-small-en-v1.5",
        )
        self.collection = self.client.get_or_create_collection(
            name="neuron_v2",   # keep original collection name to preserve existing data
            embedding_function=self.ef,
            metadata={"hnsw:space": "cosine"},
        )
        self._bm25 = None
        self._bm25_ids: list[str] = []
        self._bm25_lock = threading.Lock()

    def upsert(self, documents: list[str], metadatas: list[dict], ids: list[str], batch_size: int = 5000):
        for i in range(0, len(documents), batch_size):
            self.collection.upsert(
                documents=documents[i:i + batch_size],
                metadatas=metadatas[i:i + batch_size],
                ids=ids[i:i + batch_size],
            )
        self._bm25 = None  # invalidate in-memory BM25 cache after any write
        # Also remove the on-disk BM25 cache so next search rebuilds it
        try:
            self._bm25_cache_path().unlink(missing_ok=True)
        except Exception:
            pass

    def _bm25_cache_path(self):
        return self._data_dir / "bm25_cache.pkl"

    def _ensure_bm25(self):
        if self._bm25 is not None:
            return
        with self._bm25_lock:
            # Double-check after acquiring lock
            if self._bm25 is not None:
                return
            import pickle
            from pathlib import Path
            from rank_bm25 import BM25Okapi

            cache_path = self._bm25_cache_path()
            current_count = self.collection.count()

            # Load from disk cache if count matches (invalidated by any upsert)
            if cache_path.exists():
                try:
                    with open(cache_path, "rb") as f:
                        cached = pickle.load(f)
                    if cached.get("count") == current_count:
                        self._bm25_ids = cached["ids"]
                        self._bm25 = cached["bm25"]
                        return
                except Exception:
                    pass

            # Rebuild from scratch
            result = self.collection.get(include=["documents"])
            self._bm25_ids = result["ids"]
            import re as _re
            tokenized = [_re.sub(r"[^a-z0-9\s]", " ", doc.lower()).split() for doc in result["documents"]]
            self._bm25 = BM25Okapi(tokenized)

            # Persist to disk
            try:
                with open(cache_path, "wb") as f:
                    pickle.dump({"count": current_count, "ids": self._bm25_ids, "bm25": self._bm25}, f)
            except Exception:
                pass

    def bm25_search(self, query: str, n_results: int = 20) -> list[tuple[str, float]]:
        """Keyword search. Returns (doc_id, score) pairs sorted best-first."""
        import re as _re
        self._ensure_bm25()
        tokens = _re.sub(r"[^a-z0-9\s]", " ", query.lower()).split()
        scores = self._bm25.get_scores(tokens)
        top = sorted(enumerate(scores), key=lambda x: -x[1])[:n_results]
        return [(self._bm25_ids[i], float(s)) for i, s in top if s > 0]

    def search(self, query: str, n_results: int = 8, where: dict | None = None) -> dict:
        n_results = min(n_results, self.collection.count() or 1)
        kwargs = {"query_texts": [query], "n_results": n_results}
        if where:
            kwargs["where"] = where
        return self.collection.query(**kwargs)

    def get_by_sources(self, sources: list[str], include: list[str] | None = None) -> dict:
        """Filtered fetch — only chunks from the given sources. Much faster than full scan."""
        inc = include or ["metadatas", "documents"]
        if len(sources) == 1:
            where = {"source": sources[0]}
        else:
            where = {"source": {"$in": sources}}
        try:
            return self.collection.get(where=where, include=inc)
        except Exception:
            return {"ids": [], "documents": [], "metadatas": []}

    def count(self) -> int:
        return self.collection.count()
