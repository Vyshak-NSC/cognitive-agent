"""Deterministic, all-kind canonical cognition retrieval.

Canonical cognition remains authoritative in CognitionStore.  This module owns
search orchestration, compact projection, character budgeting, and lossless
continuations; it does not create another persisted knowledge store.
"""
from __future__ import annotations

import json
import re

from ragapp.core.semantic_index import SemanticIndex


CANONICAL_KINDS = ("entity", "relationship", "event", "location", "concept", "definition", "knowledge")


class RetrievalStore:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _tokens(query):
        return list(dict.fromkeys(re.findall(r"[A-Za-z0-9_'-]+", str(query or "").casefold())))

    @staticmethod
    def _size(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    def _current_state(self, entity):
        out = {}
        for attr in (entity.get("attributes") or {}):
            latest = self.store.latest_attribute_state(entity.get("id"), attr)
            if isinstance(latest, dict):
                out[str(attr)] = {
                    k: latest.get(k)
                    for k in ("value", "summary", "valid_from", "valid_to", "event_id")
                }
        return out

    def search_metadata_candidates(self, query, limit=8):
        """Semantic canonical candidate search with lexical compatibility fallback."""
        limit = max(1, min(int(limit or 8), 50))
        candidates = []
        try:
            index = SemanticIndex(self.store)
            records = []
            for kind in CANONICAL_KINDS:
                for rec in self.store._all_kind_records(kind):
                    ident = str(rec.get("id") or "").strip()
                    if not ident:
                        continue
                    searchable = {
                        k: rec.get(k)
                        for k in ("id", "name", "title", "type", "description", "summary", "state", "participants", "relation_type", "attributes", "effects", "location", "valid_from", "valid_to")
                        if rec.get(k) not in (None, "", [], {})
                    }
                    records.append({
                        "id": f"{kind}:{ident}",
                        "text": f"{kind}. " + json.dumps(searchable, ensure_ascii=False, default=str),
                        "metadata": {"kind": kind, "object_id": ident},
                    })
            index.sync("cognition", records)
            hits = index.search("cognition", query, limit=limit, min_score=0.20)
            for hit in hits:
                kind = hit["metadata"].get("kind")
                ident = hit["metadata"].get("object_id")
                rec = self._read_kind(kind, ident) if kind and ident else {}
                if rec:
                    candidates.append({
                        "id": str(ident), "kind": str(kind), "type": rec.get("type"),
                        "name": rec.get("name") or rec.get("title") or str(ident),
                        "summary": (rec.get("summary") or rec.get("description") or "")[:1000],
                        "provenance": rec.get("provenance", [])[:3] if isinstance(rec.get("provenance"), list) else [],
                        "semantic_score": hit["score"],
                    })
        except Exception:
            result = self.store.search_cognition_metadata(query, limit=limit)
            candidates = list(result.get("candidates", [])) if isinstance(result, dict) else []
        return {
            "query": str(query or ""),
            "candidates": candidates[:limit],
            "content_loaded": False,
            "instruction": "Hydrate only the canonical candidates needed for the answer.",
        }

    def _resolve_entity_ids(self, req):
        ids = []
        if req.get("entity_id"):
            ids.append(str(req["entity_id"]))
        ids.extend(str(x) for x in req.get("entity_ids", []) if x)
        if not ids and req.get("name"):
            ids.extend(self.store.matching_entities(req["name"]))
        return list(dict.fromkeys(ids))

    def _read_kind(self, kind, ident):
        kind = str(kind or "").lower()
        ident = str(ident)
        if kind == "entity":
            rec = self.store._read_entity(ident)
        elif kind == "event":
            rec = self.store.event(ident)
        elif kind in CANONICAL_KINDS:
            rec = self.store._read_object(kind, ident)
        else:
            rec = {}
        return dict(rec) if isinstance(rec, dict) and rec else {}

    def _project(self, kind, rec, detail="compact", req=None):
        req = req or {}
        detail = str(detail or "compact").lower()
        ident = str(rec.get("id") or "")
        base = {
            "kind": kind,
            "id": ident,
            "type": rec.get("type"),
            "name": rec.get("name") or rec.get("title") or ident,
            "summary": rec.get("summary") or rec.get("description") or "",
        }
        if kind == "entity":
            base["current_state"] = self._current_state(rec)

        if detail == "metadata":
            if kind == "entity":
                meta = dict(self.store.master_metadata().get("entities", {}).get(ident, {}))
                meta.update({"kind": "entity", "id": ident, "current_state": base.get("current_state", {})})
                return meta
            return {**base, "provenance": (rec.get("provenance") or [])[:3]}

        if detail in {"summary", "compact"}:
            # Compact means answer-oriented, not "canonical object minus provenance".
            # Attribute histories are intentionally excluded: they contain temporal
            # positions, locators and provenance and were the main query-time token leak.
            out = dict(base)
            if kind == "entity":
                state = base.get("current_state") or {}
                if state:
                    out["current_state"] = state
            for key in ("participants", "relation_type", "state", "effects", "location", "valid_from", "valid_to"):
                value = rec.get(key)
                if value not in (None, "", [], {}):
                    out[key] = value
            return out

        if detail == "state" and kind == "entity":
            state = base.get("current_state", {})
            wanted = set(map(str, req.get("attributes", [])))
            if wanted:
                state = {k: v for k, v in state.items() if k in wanted}
            return {**base, "current_state": state, "timeline": rec.get("timeline", [])}

        if detail == "section":
            sections = set(map(str, req.get("sections", [])))
            out = dict(base)
            keys = sections or {"description", "attributes", "participants", "relationships", "events", "timeline", "evolution", "provenance"}
            for key in keys:
                if key in rec:
                    out[key] = rec.get(key)
            return out

        # full: canonical object plus computed current state for entities.
        out = dict(rec)
        out["kind"] = kind
        if kind == "entity":
            out["current_state"] = self._current_state(rec)
        return out

    def _fetch(self, req):
        """Hydrate any canonical kind using a single generic request contract."""
        detail = str(req.get("detail", "compact")).lower()
        refs = []

        # Preferred generic contract.
        if req.get("kind") and req.get("id"):
            refs.append((str(req["kind"]).lower(), str(req["id"])))
        if req.get("kind") and req.get("ids"):
            refs.extend((str(req["kind"]).lower(), str(x)) for x in req.get("ids", []) if x)

        # Compatibility with existing callers.
        refs.extend(("entity", x) for x in self._resolve_entity_ids(req))
        if req.get("event_id"):
            refs.append(("event", str(req["event_id"])))
        refs.extend(("event", str(x)) for x in req.get("event_ids", []) if x)

        # Query-based hydration first uses canonical all-kind metadata search.
        if not refs and (req.get("query") or req.get("event_query")):
            query = req.get("query") or req.get("event_query")
            for candidate in self.search_metadata_candidates(query, limit=int(req.get("limit", 8) or 8))["candidates"]:
                if req.get("event_query") and candidate.get("kind") != "event":
                    continue
                refs.append((str(candidate.get("kind")), str(candidate.get("id"))))

        out = []
        seen = set()
        for kind, ident in refs:
            if kind not in CANONICAL_KINDS or (kind, ident) in seen:
                continue
            seen.add((kind, ident))
            rec = self._read_kind(kind, ident)
            if rec:
                out.append(self._project(kind, rec, detail, req))
        return out

    def _chunks(self, item, max_chars):
        """Create resumable pages while preserving every top-level field."""
        if self._size(item) <= max_chars:
            return [item]
        base = {k: item[k] for k in ("kind", "id", "type", "name", "summary", "current_state") if k in item}
        pages, page = [], dict(base)
        for key, value in item.items():
            if key in base:
                continue
            candidate = dict(page)
            candidate[key] = value
            if page != base and self._size(candidate) > max_chars:
                pages.append(page)
                page = dict(base)
                candidate = dict(page)
                candidate[key] = value
            if self._size(candidate) <= max_chars:
                page = candidate
                continue
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            step = max(256, max_chars - self._size(base) - len(key) - 256)
            for pos in range(0, len(text), step):
                if page != base:
                    pages.append(page)
                    page = dict(base)
                fragment = dict(base)
                fragment[key] = text[pos : pos + step]
                pages.append(fragment)
        if page != base or not pages:
            pages.append(page)
        total = len(pages)
        return [
            {**p, "chunk": {"index": i + 1, "count": total, "of_object": item.get("id"), "kind": item.get("kind")}}
            for i, p in enumerate(pages)
        ]

    def retrieve(self, requests, max_chars=12000):
        """Budgeted retrieval with lossless continuation semantics.

        Every unreturned page/request is represented in next_requests.  The
        caller can therefore continue deterministically without asking the LLM.
        """
        max_chars = max(1000, int(max_chars or 12000))
        source_requests = [dict(r) for r in (requests or []) if isinstance(r, dict)]
        result = {"requests": [], "used_chars": 0, "truncated": False}
        next_requests = []

        for req_pos, req in enumerate(source_requests):
            items = self._fetch(req)
            pages = []
            for item in items:
                if str(req.get("detail", "compact")).lower() == "full":
                    pages.extend(self._chunks(item, max_chars))
                else:
                    pages.append(item)
            start = max(0, int(req.get("chunk_index", 0) or 0))

            stopped = False
            for index in range(start, len(pages)):
                item = pages[index]
                size = self._size(item)
                if result["requests"] and result["used_chars"] + size > max_chars:
                    next_requests.append({**req, "chunk_index": index})
                    stopped = True
                    break
                result["requests"].append(item)
                result["used_chars"] += size
                if result["used_chars"] >= max_chars and index + 1 < len(pages):
                    next_requests.append({**req, "chunk_index": index + 1})
                    stopped = True
                    break

            if stopped:
                # Preserve every later original request exactly once.
                next_requests.extend(source_requests[req_pos + 1 :])
                break

        # Stable de-duplication prevents controller continuation loops.
        deduped = []
        seen = set()
        for req in next_requests:
            key = json.dumps(req, ensure_ascii=False, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                deduped.append(req)
        if deduped:
            result["truncated"] = True
            result["next_requests"] = deduped
        return result

    def retrieve_candidates(self, candidates, detail="compact", max_chars=8000):
        requests = [
            {"kind": c.get("kind"), "id": c.get("id"), "detail": detail}
            for c in (candidates or [])
            if c.get("kind") in CANONICAL_KINDS and c.get("id")
        ]
        return self.retrieve(requests, max_chars=max_chars)

    def retrieve_for_query(self, query, max_chars=8000, limit=8, detail="compact"):
        candidates = self.search_metadata_candidates(query, limit=limit).get("candidates", [])
        result = self.retrieve_candidates(candidates, detail=detail, max_chars=max_chars)
        result["query"] = str(query or "")
        result["candidates"] = candidates
        return result

    def hybrid_search(self, query, top_n=12):
        return self.search_metadata_candidates(query, top_n).get("candidates", [])

    def search(self, query, top_n=12):
        return self.hybrid_search(query, top_n)

    def search_metadata(self, query, limit=20):
        return self.search_metadata_candidates(query, limit).get("candidates", [])
