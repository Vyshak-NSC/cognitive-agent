"""Local, zero-API-call retrieval over cognition metadata and source chunks.
Embeddings are intentionally not used here. The agent first asks for compact metadata,
then requests only the entity/section/timeline detail it actually needs."""
from __future__ import annotations
import json
from ragapp.core.metadata import MetadataDB

class RetrievalStore:
    def __init__(self,store):
        self.store=store; self.db=MetadataDB(store)

    def _expanded_tokens(self, query):
        return [t for t in set(str(query).lower().replace("-"," ").split()) if t]

    def _rank_candidates(self, query, limit=20):
        expanded = set(self._expanded_tokens(query))
        if not expanded:
            return []

        scored = []
        for entity_id, meta in self.store.master_metadata().get("entities", {}).items():
            hay = " ".join([
                str(meta.get("name") or ""),
                str(meta.get("summary") or ""),
                str(meta.get("type") or ""),
                " ".join(str(x) for x in (meta.get("tags") or [])),
                str(meta.get("path") or "")
            ]).lower()
            score = 0
            for token in expanded:
                token_score = 0
                if token in (meta.get("name") or "").lower():
                    token_score += 20
                if token in (meta.get("summary") or "").lower():
                    token_score += 8
                if token in (meta.get("type") or "").lower():
                    token_score += 6
                if token in str(meta.get("path") or "").lower():
                    token_score += 4
                if token in " ".join(str(x) for x in (meta.get("tags") or [])).lower():
                    token_score += 7
                score += token_score
            if score > 0:
                scored.append((entity_id, score, meta))

        scored.sort(key=lambda item: (-item[1], item[0]))
        return [item[0] for item in scored[:limit]]

    def search_metadata(self,query,limit=20):
        tokens=[x for x in str(query).lower().split() if x]
        rows=self.db.search_entities(tokens,limit=limit)
        if not rows:
            return []
        scored=[]
        for row in rows:
            text=" ".join([str(row.get("name") or ""), str(row.get("summary") or ""), str(row.get("description") or ""), str(row.get("tags") or "")]).lower()
            score = 0
            for each in set(tokens):
                if each in text:
                    score += 3
            scored.append((score,row))
        scored.sort(key=lambda item: (-item[0], item[1].get("name", "")))
        return [row for _, row in scored[:limit]]

    def retrieve(self,requests,max_chars=12000):
        return self.store.retrieve(requests,max_chars=max_chars)

    def retrieve_for_query(self,query,max_chars=12000):
        """Build one bounded cognition package locally for the user's query.

        This version keeps the legacy API but upgrades the candidate selection to a
        bounded hybrid strategy: lexical token weighting + entity metadata scoring +
        fallback to the older matching path. No external embedding service is used.
        """
        entity_ids=self._rank_candidates(query, limit=20)
        if not entity_ids:
            entity_ids=self.store.matching_entities(query)[:20]

        requests=[
            {
                "entity_id":eid,
                "detail":"state",
                "timeline":"latest",
            }
            for eid in entity_ids
        ]

        if not requests:
            return {
                "requests":[],
                "relationships":[],
                "used_chars":0,
                "truncated":False
            }

        return self.retrieve(
            requests,
            max_chars=max_chars
        )

    def search(self,query,top_n=12):
        """Compatibility search. Returns compact metadata first; source text is not loaded."""
        rows=self.search_metadata(query,limit=top_n)
        return [{"entity_id":r["id"],"source_file":r.get("path"),"category":r.get("type"),"content":r.get("summary",""),"score":1.0} for r in rows]

    def hybrid_search(self, query, top_n=12):
        """Local hybrid retrieval using metadata ranking, query expansion, and reranked candidate selection."""
        entity_ids=self._rank_candidates(query, limit=top_n)
        if not entity_ids:
            entity_ids=self.store.matching_entities(query)[:top_n]

        out=[]
        for eid in entity_ids:
            meta=self.store.master_metadata().get("entities", {}).get(eid)
            if not meta:
                continue
            out.append({
                "entity_id": eid,
                "source_file": meta.get("path"),
                "category": meta.get("type"),
                "content": meta.get("summary", ""),
                "score": 1.0,
            })
        return out
