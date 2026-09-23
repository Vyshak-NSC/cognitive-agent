"""Local, zero-API-call retrieval over cognition metadata.

The query path is deliberately metadata-first. Source bodies are never loaded by
this module; source retrieval is a separate, explicit operation.
"""
from __future__ import annotations
import re
from ragapp.core.metadata import MetadataDB


class RetrievalStore:
    def __init__(self, store):
        self.store = store
        self.db = MetadataDB(store)

    @staticmethod
    def _tokens(query):
        return [x for x in re.findall(r"[\w.-]+", str(query).lower()) if x]

    @staticmethod
    def _phrases(query):
        text = " ".join(RetrievalStore._tokens(query))
        tokens = text.split()
        phrases = [text] if len(tokens) > 1 else []
        phrases.extend(" ".join(tokens[i:i+2]) for i in range(len(tokens)-1))
        return list(dict.fromkeys(p for p in phrases if p))

    def _rank_entities(self, query, limit=8):
        q = " ".join(self._tokens(query))
        tokens = set(self._tokens(query))
        phrases = self._phrases(query)
        scored = []
        for entity_id, meta in self.store.master_metadata().get("entities", {}).items():
            name = str(meta.get("name") or "").lower()
            summary = str(meta.get("summary") or "").lower()
            typ = str(meta.get("type") or "").lower()
            tags = " ".join(str(x) for x in (meta.get("tags") or [])).lower()
            path = str(meta.get("path") or "").lower()
            hay = " ".join((name, summary, typ, tags, path))
            score = 0
            for phrase in phrases:
                if phrase in name:
                    score += 100
                elif phrase in summary:
                    score += 30
                elif phrase in tags:
                    score += 20
            for token in tokens:
                if token in name:
                    score += 15
                if token in summary:
                    score += 4
                if token in typ:
                    score += 3
                if token in tags:
                    score += 5
            if q and q == name:
                score += 250
            if score:
                scored.append((score, entity_id, meta))
        scored.sort(key=lambda x: (-x[0], str(x[2].get("name") or ""), x[1]))
        return scored[:limit]

    def search_metadata(self, query, limit=8):
        """Return compact candidate metadata only: entities + document sections."""
        candidates = []
        for score, eid, meta in self._rank_entities(query, limit=limit):
            candidates.append({
                "kind": "entity", "id": eid, "score": score,
                "name": meta.get("name"), "type": meta.get("type"),
                "summary": meta.get("summary", ""),
                "tags": meta.get("tags", []), "path": meta.get("path"),
            })

        q = " ".join(self._tokens(query))
        section_rows = self.store.document_index.sections(query=None, limit=5000)
        section_scored = []
        for section in section_rows:
            title = str(section.get("title") or "").lower()
            sid = str(section.get("id") or "").lower()
            if not title and not sid:
                continue
            score = 0
            if q and q == title:
                score += 300
            for phrase in self._phrases(query):
                if phrase in title:
                    score += 120
            for token in set(self._tokens(query)):
                if token in title:
                    score += 18
            if score:
                section_scored.append((score, section))
        section_scored.sort(key=lambda x: (-x[0], x[1].get("order", 0)))
        for score, section in section_scored[:limit]:
            candidates.append({
                "kind": "section", "id": section.get("id"), "score": score,
                "name": section.get("title"), "type": section.get("type"),
                "summary": "document section",
                "artifact_id": section.get("artifact_id"),
                "path": section.get("path"), "locator": section.get("locator", {}),
                "parent_id": section.get("parent_id"),
            })
        candidates.sort(key=lambda x: (-x.get("score", 0), x.get("kind", ""), x.get("name") or ""))
        return candidates[:limit]

    def retrieve(self, requests, max_chars=12000):
        return self.store.retrieve(requests, max_chars=max_chars)

    def retrieve_for_query(self, query, max_chars=12000):
        """Legacy compatibility method; returns metadata-only candidates."""
        candidates = self.search_metadata(query, limit=8)
        return {"candidates": candidates, "used_chars": len(str(candidates)), "truncated": False}

    def search(self, query, top_n=12):
        return self.search_metadata(query, limit=top_n)

    def hybrid_search(self, query, top_n=12):
        return self.search_metadata(query, limit=top_n)
