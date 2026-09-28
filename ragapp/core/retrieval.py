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

# Single source of truth for what text represents a cognition object in the
# semantic index. Both the per-turn context selector and the
# search_cognition_metadata tool write the SAME "cognition" namespace; when they
# built different text, each overwrote (and re-embedded) the other's records.
INDEX_FIELDS = (
    "id", "name", "title", "type", "tags", "description", "summary", "state",
    "participants", "relation_type", "effects", "location", "valid_from", "valid_to",
)

# A group tag made only of these words is too generic to define a group.
_GROUP_STOPWORDS = frozenset({
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "is", "are", "was", "were",
    "list", "all", "name", "what", "who", "which", "show", "me", "tell", "about", "give",
    "their", "them", "these", "those", "every", "each", "many", "how", "there", "any",
    "other", "thing", "entity", "character",
})
# A tag shared by more entities than this is a topic, not a listable group.
_MAX_GROUP_SIZE = 25


def cognition_index_records(store):
    """Records for the semantic 'cognition' namespace (identical for every caller)."""
    records = []
    for kind in CANONICAL_KINDS:
        for rec in store._all_kind_records(kind):
            ident = str(rec.get("id") or "").strip()
            if not ident:
                continue
            searchable = {k: rec.get(k) for k in INDEX_FIELDS if rec.get(k) not in (None, "", [], {})}
            records.append({
                "id": f"{kind}:{ident}",
                "text": f"{kind}. " + json.dumps(searchable, ensure_ascii=False, default=str),
                "metadata": {"kind": kind, "object_id": ident},
            })
    return records


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

    @staticmethod
    def _norm_words(text):
        """Lower-cased word list with '_' split and a light plural strip (beasts -> beast)."""
        words = re.findall(r"[a-z0-9]+", str(text or "").casefold().replace("_", " "))
        return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words]

    @staticmethod
    def _contains_phrase(tokens, phrase):
        n = len(phrase)
        return n > 0 and any(tokens[i:i + n] == phrase for i in range(len(tokens) - n + 1))

    def group_candidates(self, query, limit=25):
        """Deterministic lexical candidates: entities named in, or grouped by a tag named in, the query.

        Example: "list the god beast names" contains the phrase "god beast",
        which is a tag on every God Beast entity, so all of them are returned
        regardless of how the embedding model ranks them against each other.
        """
        tokens = self._norm_words(query)
        if not tokens:
            return []
        entities = self.store.master_metadata().get("entities", {}) or {}
        name_hits, tag_groups = [], {}
        for eid, meta in entities.items():
            name_words = self._norm_words(meta.get("name"))
            if name_words and self._contains_phrase(tokens, name_words):
                name_hits.append(eid)
            seen = set()
            for tag in meta.get("tags") or []:
                words = tuple(self._norm_words(tag))
                if not words or words in seen or all(w in _GROUP_STOPWORDS for w in words):
                    continue
                seen.add(words)
                if self._contains_phrase(tokens, list(words)):
                    tag_groups.setdefault(words, []).append(eid)

        ordered = [(eid, "name") for eid in sorted(name_hits)]
        # More specific (longer) tag phrases first, then smaller groups.
        for words, members in sorted(tag_groups.items(), key=lambda kv: (-len(kv[0]), len(kv[1]), kv[0])):
            if len(members) > _MAX_GROUP_SIZE:
                continue
            ordered.extend((eid, "tag:" + " ".join(words)) for eid in sorted(members))

        out, seen_ids = [], set()
        for eid, why in ordered:
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            meta = entities.get(eid) or {}
            out.append({
                "kind": "entity", "id": str(eid), "type": meta.get("type"),
                "name": meta.get("name") or str(eid),
                "summary": str(meta.get("summary") or "")[:1000],
                "provenance": [], "semantic_score": None, "match": why,
            })
            if len(out) >= limit:
                break
        return out

    def search_metadata_candidates(self, query, limit=8):
        """Semantic canonical candidate search with lexical compatibility fallback."""
        limit = max(1, min(int(limit or 8), 50))
        candidates = []
        try:
            index = SemanticIndex(self.store)
            records = cognition_index_records(self.store)
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
        candidates = candidates[:limit]
        # Semantic top-k cannot enumerate a group ("list the God Beasts") because
        # several members compete for the same few slots. Add every entity whose
        # tag/name is named in the query, on top of the semantic hits.
        try:
            have = {(str(c.get("kind")), str(c.get("id"))) for c in candidates}
            for extra in self.group_candidates(query):
                key = (extra["kind"], extra["id"])
                if key not in have:
                    have.add(key)
                    candidates.append(extra)
        except Exception:
            pass
        return {
            "query": str(query or ""),
            "candidates": candidates[:50],
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

    @staticmethod
    def _reference_ids(value):
        """Yield stable ids from the reference shapes used by canonical cognition."""
        if isinstance(value, str):
            if value.strip():
                yield value.strip()
            return
        if isinstance(value, dict):
            ident = value.get("id") or value.get("entity_id") or value.get("event_id")
            if ident:
                yield str(ident)
            return
        if isinstance(value, list):
            for item in value:
                yield from RetrievalStore._reference_ids(item)

    def expand_candidates(self, candidates, max_depth=1, max_items=24):
        """Bounded deterministic graph expansion after semantic candidate selection.

        Embeddings locate an entry point; canonical references determine its semantic
        neighbourhood.  This prevents top-k similarity from being mistaken for a
        complete set of related project knowledge.
        """
        kind_for_field = {
            "relationships": "relationship", "events": "event", "locations": "location",
            "concepts": "concept", "definitions": "definition", "knowledge_links": "knowledge",
            "related_entity_ids": "entity", "entity_ids": "entity", "entities": "entity",
            "related_event_ids": "event", "event_ids": "event",
            "related_concept_ids": "concept", "concept_ids": "concept",
            "related_location_ids": "location", "location_ids": "location",
            "related_relationship_ids": "relationship", "relationship_ids": "relationship",
        }
        ordered, seen, frontier = [], set(), []
        for candidate in candidates or []:
            kind, ident = str(candidate.get("kind") or ""), str(candidate.get("id") or "")
            if kind in CANONICAL_KINDS and ident and (kind, ident) not in seen:
                seen.add((kind, ident)); ordered.append(dict(candidate)); frontier.append((kind, ident))
        depth = 0
        while frontier and depth < max(0, int(max_depth or 0)) and len(ordered) < max_items:
            next_frontier = []
            selected_ids = {ident for _, ident in frontier}
            for kind, ident in frontier:
                rec = self._read_kind(kind, ident) or {}
                for field, target_kind in kind_for_field.items():
                    for ref_id in self._reference_ids(rec.get(field)):
                        key = (target_kind, ref_id)
                        if key in seen or not self._read_kind(*key):
                            continue
                        seen.add(key); ordered.append({"kind": target_kind, "id": ref_id, "score": None, "match": "graph"})
                        next_frontier.append(key)
                        if len(ordered) >= max_items: break
                    if len(ordered) >= max_items: break
                if len(ordered) >= max_items: break
                # Participant records are typed references and may point to entities
                # or other canonical objects.
                for participant in rec.get("participants") or []:
                    if not isinstance(participant, dict) or not participant.get("id"): continue
                    pk = str(participant.get("kind") or "entity").rstrip("s")
                    key = (pk, str(participant["id"]))
                    if pk in CANONICAL_KINDS and key not in seen and self._read_kind(*key):
                        seen.add(key); ordered.append({"kind": pk, "id": key[1], "score": None, "match": "graph"}); next_frontier.append(key)
                        if len(ordered) >= max_items: break
            # Backlinks matter when only the relationship/event knows about the
            # selected object. Scan canonical records locally; no LLM/API call.
            if len(ordered) < max_items and selected_ids:
                for other_kind in CANONICAL_KINDS:
                    for rec in self.store._all_kind_records(other_kind):
                        oid = str(rec.get("id") or "")
                        key = (other_kind, oid)
                        if not oid or key in seen: continue
                        blob = json.dumps({k: rec.get(k) for k in (*kind_for_field.keys(), "participants") if rec.get(k)}, ensure_ascii=False, default=str)
                        if any(ref_id in blob for ref_id in selected_ids):
                            seen.add(key); ordered.append({"kind": other_kind, "id": oid, "score": None, "match": "backlink"}); next_frontier.append(key)
                            if len(ordered) >= max_items: break
                    if len(ordered) >= max_items: break
            frontier = next_frontier
            depth += 1
        return ordered

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