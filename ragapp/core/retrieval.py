"""Zero-API-call metadata retrieval over the canonical cognition model."""
from __future__ import annotations
import re


class RetrievalStore:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _tokens(query):
        return [x for x in re.findall(r"[\w.-]+", str(query).lower()) if x]

    @staticmethod
    def _phrases(query):
        tokens = RetrievalStore._tokens(query)
        text = " ".join(tokens)
        phrases = [text] if len(tokens) > 1 else []
        phrases.extend(" ".join(tokens[i:i+2]) for i in range(len(tokens) - 1))
        return list(dict.fromkeys(x for x in phrases if x))

    def search_metadata(self, query, limit=8):
        """Search canonical cognition + structural metadata; never reads source bodies."""
        q = str(query or "").strip().casefold()
        tokens = set(self._tokens(query))
        phrases = self._phrases(query)
        scored = []

        # Canonical cognition candidates.
        for kind in ("entity", "relationship", "event", "location", "concept", "definition", "knowledge"):
            for rec in self.store._all_kind_records(kind):
                name = str(rec.get("name") or rec.get("title") or rec.get("term") or rec.get("id") or "").casefold()
                text = " ".join(str(rec.get(k, "")) for k in ("name", "title", "term", "description", "summary", "state", "type")).casefold()
                score = 0
                if q and q == name:
                    score += 400
                if q and q in name:
                    score += 180
                if q and q in text:
                    score += 80
                for phrase in phrases:
                    if phrase in name:
                        score += 100
                    elif phrase in text:
                        score += 35
                for token in tokens:
                    if token in name:
                        score += 20
                    elif token in text:
                        score += 4
                if score:
                    scored.append({
                        "kind": kind,
                        "id": rec.get("id"),
                        "score": score,
                        "name": rec.get("name") or rec.get("title") or rec.get("term") or rec.get("id"),
                        "type": rec.get("type"),
                        "summary": (rec.get("summary") or rec.get("description") or "")[:1000],
                        "provenance": (rec.get("provenance") or [])[:3] if isinstance(rec.get("provenance"), list) else [],
                    })

        # Structural document candidates.
        for section in self.store.document_index.sections(query=None, limit=5000):
            title = str(section.get("title") or "").casefold()
            score = 0
            if q and q == title:
                score += 500
            if q and q in title:
                score += 220
            for phrase in phrases:
                if phrase in title:
                    score += 120
            for token in tokens:
                if token in title:
                    score += 18
            if score:
                scored.append({
                    "kind": "section",
                    "id": section.get("id"),
                    "score": score,
                    "name": section.get("title"),
                    "type": section.get("type"),
                    "summary": "document structure node",
                    "artifact_id": section.get("artifact_id"),
                    "path": section.get("path"),
                    "locator": section.get("locator", {}),
                    "parent_id": section.get("parent_id"),
                })

        scored.sort(key=lambda x: (-x.get("score", 0), x.get("kind", ""), str(x.get("name") or "")))
        return scored[:max(1, min(int(limit or 8), 50))]

    def retrieve(self, requests, max_chars=12000):
        return self.store.retrieve(requests, max_chars=max_chars)

    def retrieve_for_query(self, query, max_chars=12000):
        candidates = self.search_metadata(query, limit=8)
        return {"candidates": candidates, "used_chars": len(str(candidates)), "truncated": False}

    def search(self, query, top_n=12):
        return self.search_metadata(query, limit=top_n)

    def hybrid_search(self, query, top_n=12):
        return self.search_metadata(query, limit=top_n)
