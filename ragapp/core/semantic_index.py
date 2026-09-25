"""Local semantic indexing for context/tool/cognition selection.

This module deliberately performs no generative/API inference.  A small local
embedding model maps query text and index records into the same vector space;
Python only computes cosine similarity over those vectors.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path

import numpy as np

_DEFAULT_MODEL = os.getenv("CPA_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
_MODEL = None
_MODEL_LOCK = threading.Lock()


def _encoder():
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                try:
                    from fastembed import TextEmbedding
                except ImportError as exc:  # fail closed: lexical fallback lives in callers
                    raise RuntimeError(
                        "Local semantic retrieval requires fastembed. Run `uv sync` after applying the patch."
                    ) from exc
                _MODEL = TextEmbedding(model_name=_DEFAULT_MODEL)
    return _MODEL


def _embed(texts):
    values = [str(x or "") for x in texts]
    if not values:
        return []
    return [np.asarray(v, dtype=np.float32) for v in _encoder().embed(values)]


def _digest(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


class SemanticIndex:
    """Persistent project-local embedding index with incremental upserts."""

    def __init__(self, store):
        root = Path(store.root) / ".system"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "semantic_index.db"
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS semantic_items(
                    namespace TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    model TEXT NOT NULL,
                    vector BLOB NOT NULL,
                    dims INTEGER NOT NULL,
                    PRIMARY KEY(namespace, item_id)
                )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_semantic_namespace ON semantic_items(namespace)")

    def sync(self, namespace, records):
        """Upsert changed records and delete stale records in one namespace."""
        normalized = []
        for rec in records or []:
            item_id = str(rec.get("id") or "").strip()
            text = str(rec.get("text") or "").strip()
            if item_id and text:
                normalized.append((item_id, text, dict(rec.get("metadata") or {})))
        wanted = {item_id for item_id, _, _ in normalized}
        with self._connect() as conn:
            existing = {
                row["item_id"]: row["content_hash"]
                for row in conn.execute(
                    "SELECT item_id, content_hash FROM semantic_items WHERE namespace=? AND model=?",
                    (namespace, _DEFAULT_MODEL),
                )
            }
        changed = [(i, t, m) for i, t, m in normalized if existing.get(i) != _digest(t)]
        vectors = _embed([t for _, t, _ in changed]) if changed else []
        with self._connect() as conn:
            for (item_id, text, metadata), vector in zip(changed, vectors):
                conn.execute(
                    """INSERT INTO semantic_items(namespace,item_id,text,content_hash,metadata,model,vector,dims)
                       VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(namespace,item_id) DO UPDATE SET
                         text=excluded.text, content_hash=excluded.content_hash,
                         metadata=excluded.metadata, model=excluded.model,
                         vector=excluded.vector, dims=excluded.dims""",
                    (
                        namespace, item_id, text, _digest(text),
                        json.dumps(metadata, ensure_ascii=False, default=str),
                        _DEFAULT_MODEL, vector.tobytes(), int(vector.size),
                    ),
                )
            if wanted:
                placeholders = ",".join("?" for _ in wanted)
                conn.execute(
                    f"DELETE FROM semantic_items WHERE namespace=? AND item_id NOT IN ({placeholders})",
                    (namespace, *sorted(wanted)),
                )
            else:
                conn.execute("DELETE FROM semantic_items WHERE namespace=?", (namespace,))

    def embed_query(self, query):
        query = str(query or "").strip()
        return _embed([query])[0] if query else None

    def search(self, namespace, query=None, limit=8, min_score=0.0, query_vector=None):
        query = str(query or "").strip()
        qvec = query_vector if query_vector is not None else (self.embed_query(query) if query else None)
        if qvec is None:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT item_id,text,metadata,vector,dims FROM semantic_items WHERE namespace=? AND model=?",
                (namespace, _DEFAULT_MODEL),
            ).fetchall()
        scored = []
        qnorm = float(np.linalg.norm(qvec)) or 1.0
        for row in rows:
            vec = np.frombuffer(row["vector"], dtype=np.float32, count=int(row["dims"]))
            if vec.size != qvec.size:
                continue
            denom = qnorm * (float(np.linalg.norm(vec)) or 1.0)
            score = float(np.dot(qvec, vec) / denom)
            if score >= float(min_score):
                scored.append({
                    "id": row["item_id"],
                    "score": score,
                    "text": row["text"],
                    "metadata": json.loads(row["metadata"] or "{}"),
                })
        scored.sort(key=lambda x: (-x["score"], x["id"]))
        return scored[: max(1, int(limit or 8))]
