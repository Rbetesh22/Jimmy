#!/usr/bin/env python3
"""
Rebuild the HNSW vector index from existing documents in ChromaDB SQLite.
Run once after the corrupted Rust-written HNSW files were removed.
"""
import sys
import time

sys.path.insert(0, "/Users/ralphbetesh/jimmy")

from jimmy.storage.store import JimmyStore
from jimmy.config import CHROMA_DIR

BATCH_SIZE = 500

def main():
    print("Connecting to store...", flush=True)
    store = JimmyStore(CHROMA_DIR)
    total = store.count()
    print(f"Total documents in SQLite: {total}", flush=True)

    print("Loading all documents from SQLite...", flush=True)
    t0 = time.time()
    result = store.collection.get(include=["documents", "metadatas"])
    ids = result["ids"]
    docs = result["documents"]
    metas = result["metadatas"]
    print(f"Loaded {len(ids)} documents in {time.time()-t0:.1f}s", flush=True)

    print(f"Rebuilding HNSW index in batches of {BATCH_SIZE}...", flush=True)
    t0 = time.time()
    for i in range(0, len(ids), BATCH_SIZE):
        batch_ids = ids[i:i+BATCH_SIZE]
        batch_docs = docs[i:i+BATCH_SIZE]
        batch_metas = metas[i:i+BATCH_SIZE]
        store.collection.upsert(
            documents=batch_docs,
            metadatas=batch_metas,
            ids=batch_ids,
        )
        done = i + len(batch_ids)
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0
        eta = (len(ids) - done) / rate if rate > 0 else 0
        print(f"  {done}/{len(ids)} ({100*done/len(ids):.1f}%) "
              f"| {rate:.0f} docs/s | ETA {eta/60:.1f}m", flush=True)

    print(f"\nDone! Rebuilt index for {len(ids)} documents.", flush=True)

if __name__ == "__main__":
    main()
