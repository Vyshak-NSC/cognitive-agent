"""Local metadata-only retrieval.

This module deliberately separates candidate discovery from content retrieval.
The LLM receives compact metadata candidates first. Source/entity content is
loaded only by a later, explicit tool call.
"""
from __future__ import annotations
import json
import re
from ragapp.core.metadata import MetadataDB


class RetrievalStore:
    def __init__(self, store):
        self.store = store
        self.db = MetadataDB(store)

    def _expanded_tokens(self, query):
        raw = re.findall(r"[A-Za-z0-9_'-]+", str(query).lower())
        return [t for t in dict.fromkeys(raw) if len(t) > 1]

    def _rank_candidates(self, query, limit=20):
        expanded = set(self._expanded_tokens(query))
        if not expanded:
            return []

        scored = []
        for entity_id, meta in self.store.master_metadata().get("entities", {}).items():
            name = str(meta.get("name") or "").lower()
            summary = str(meta.get("summary") or "").lower()
            description = str(meta.get("description") or "").lower()
            type_name = str(meta.get("type") or "").lower()
            tags = " ".join(str(x) for x in (meta.get("tags") or [])).lower()
            path = str(meta.get("path") or "").lower()
            score = 0
            for token in expanded:
                if token in name:
                    score += 20
                if token in summary:
                    score += 8
                if token in description:
                    score += 5
                if token in type_name:
                    score += 4
                if token in tags:
                    score += 6
                if token in path:
                    score += 4
            if score:
                scored.append((entity_id, score, meta))

        scored.sort(key=lambda item: (-item[1], item[0]))
        return [item[0] for item in scored[:limit]]

    def _rank_artifacts(self, query, limit=8):
        tokens = set(self._expanded_tokens(query))
        artifacts = self.store.master_metadata().get("artifacts", {})
        scored = []
        for artifact_id, meta in artifacts.items():
            if meta.get("area") != "source":
                continue
            name = str(meta.get("name") or "").lower()
            path = str(meta.get("path") or "").lower()
            summary = str(meta.get("summary") or "").lower()
            description = str(meta.get("description") or "").lower()
            kind = str(meta.get("type") or "").lower()
            score = 0
            for token in tokens:
                if token in name:
                    score += 12
                if token in path:
                    score += 8
                if token in summary:
                    score += 5
                if token in description:
                    score += 4
                if token in kind:
                    score += 2
            if score:
                scored.append((artifact_id, score, meta))

        # If the query is explicitly about pages/chapters and no filename was
        # mentioned, expose a small set of source PDFs as candidates. This is
        # metadata only; no PDF text is loaded here.
        source_pdfs = [
            (aid, 1, meta)
            for aid, meta in artifacts.items()
            if meta.get("area") == "source"
            and str(meta.get("type") or "").lower() == "pdf"
        ]
        if any(t in tokens for t in {"page", "pages", "chapter", "section", "pdf"}):
            scored.extend(source_pdfs)

        scored.sort(key=lambda item: (-item[1], str(item[2].get("path") or "")))
        out = []
        seen = set()
        for artifact_id, _, meta in scored:
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            out.append({
                "kind": "artifact",
                "id": artifact_id,
                "path": meta.get("path"),
                "name": meta.get("name"),
                "type": meta.get("type"),
                "summary": meta.get("summary", ""),
                "description": meta.get("description", ""),
            })
            if len(out) >= limit:
                break
        return out

    def _entity_candidate(self, entity_id):
        meta = self.store.master_metadata().get("entities", {}).get(entity_id)
        if not meta:
            return None
        entity = self.store._read_entity(entity_id)
        locators = []
        source_artifacts = []
        for attr_states in (entity.get("attributes") or {}).values():
            for state in attr_states or []:
                if not isinstance(state, dict):
                    continue
                locator = state.get("source_locator") or state.get("locator")
                artifact = state.get("source_artifact")
                if locator:
                    locators.append({"artifact": artifact, "locator": locator})
                if artifact:
                    source_artifacts.append(artifact)
        for event in entity.get("events") or []:
            if not isinstance(event, dict):
                continue
            locator = event.get("source_locator") or event.get("locator")
            artifact = event.get("source_artifact")
            if locator:
                locators.append({"artifact": artifact, "locator": locator})
            if artifact:
                source_artifacts.append(artifact)

        artifacts = self.store.master_metadata().get("artifacts", {})
        artifact_refs = []
        for aid in dict.fromkeys(source_artifacts):
            item = artifacts.get(aid)
            if item:
                artifact_refs.append({
                    "id": aid,
                    "path": item.get("path"),
                    "type": item.get("type"),
                })

        return {
            "kind": "entity",
            "id": entity_id,
            "name": meta.get("name"),
            "type": meta.get("type"),
            "summary": meta.get("summary", ""),
            "tags": meta.get("tags", []),
            "path": meta.get("path"),
            "source_artifacts": artifact_refs,
            "source_locators": locators[:12],
        }

    def search_metadata_candidates(self, query, limit=8):
        """Return compact candidate metadata only; never return source content."""
        limit = max(1, min(int(limit), 20))
        candidates = []
        for entity_id in self._rank_candidates(query, limit=limit):
            candidate = self._entity_candidate(entity_id)
            if candidate:
                candidates.append(candidate)
            if len(candidates) >= limit:
                break

        remaining = max(0, limit - len(candidates))
        if remaining:
            candidates.extend(self._rank_artifacts(query, limit=remaining))

        return {
            "query": str(query),
            "candidates": candidates[:limit],
            "content_loaded": False,
            "instruction": "Use these candidates to choose or refine the next targeted retrieval. No source content was loaded.",
        }

    def search_metadata(self, query, limit=20):
        tokens = [x for x in str(query).lower().split() if x]
        rows = self.db.search_entities(tokens, limit=limit)
        if not rows:
            return []
        return rows[:limit]

    # Legacy methods retained for compatibility. They are no longer used as
    # eager context injection by the agent loop.
    def retrieve(self, requests, max_chars=12000):
        """Retrieve targeted canonical cognition; temporal resolution stays in the canonical store."""
        result = {"requests": []}
        used = 0

        def temporal_request(req):
            if not isinstance(req, dict):
                return None
            if isinstance(req.get("temporal_position"), dict):
                return req["temporal_position"]
            if isinstance(req.get("story_time"), dict) or isinstance(req.get("narrative_position"), dict):
                return {
                    "temporal_position": {
                        "story": req.get("story_time") if isinstance(req.get("story_time"), dict) else {},
                        "narrative": req.get("narrative_position") if isinstance(req.get("narrative_position"), dict) else {},
                    }
                }
            if isinstance(req.get("as_of"), dict):
                return req["as_of"]
            return None

        for req in requests or []:
            if not isinstance(req, dict):
                continue
            detail = str(req.get("detail", "summary")).lower()
            as_of = temporal_request(req)
            ids = []
            if req.get("entity_id"):
                ids = [str(req["entity_id"])]
            elif req.get("entity_ids"):
                ids = [str(x) for x in req["entity_ids"]]
            elif req.get("name") or req.get("query"):
                ids = self._rank_candidates(req.get("name") or req.get("query"), limit=20)

            for eid in ids[:20]:
                entity = self.store._read_entity(eid)
                if not entity:
                    continue

                item = {
                    "id": eid,
                    "type": entity.get("type"),
                    "name": entity.get("name"),
                    "summary": entity.get("summary", ""),
                    "description": entity.get("description", ""),
                    "tags": entity.get("tags", []),
                }

                if detail in {"state", "section", "full"}:
                    item["knowledge"] = entity.get("knowledge", {})
                    item["provenance"] = entity.get("provenance", [])
                    item["timeline"] = entity.get("timeline", [])
                    if as_of is not None:
                        resolution = self.store.resolve_entity_state(
                            eid, as_of, attributes=req.get("attributes")
                        )
                        item["attributes"] = resolution.get("attributes", {})
                        item["temporal_resolution"] = {
                            "requested": as_of,
                            "found": resolution.get("found", False),
                        }
                        if resolution.get("reason"):
                            item["temporal_resolution"]["reason"] = resolution["reason"]
                    else:
                        item["attributes"] = entity.get("attributes", {})

                if detail in {"full", "section"}:
                    item["relationship_refs"] = entity.get("relationships", [])
                    item["event_refs"] = entity.get("events", [])
                    item["location_refs"] = entity.get("locations", [])
                    item["concept_refs"] = entity.get("concepts", [])
                if detail == "full":
                    item["relationships"] = [
                        self.store.relationships().get(r.get("id"))
                        for r in entity.get("relationships", [])
                        if isinstance(r, dict) and self.store.relationships().get(r.get("id"))
                    ]
                    item["events"] = [
                        self.store.event(r.get("id"))
                        for r in entity.get("events", [])
                        if isinstance(r, dict) and self.store.event(r.get("id"))
                    ]
                    item["locations"] = [
                        self.store._read_object("location", r.get("id"))
                        for r in entity.get("locations", [])
                        if isinstance(r, dict) and self.store._read_object("location", r.get("id"))
                    ]

                payload = json.dumps(item, ensure_ascii=False)
                if used + len(payload) > max_chars:
                    break
                result["requests"].append(item)
                used += len(payload)

        result["used_chars"] = used
        result["truncated"] = used >= max_chars
        return result

    def retrieve_for_query(self, query, max_chars=12000):
        entity_ids = self._rank_candidates(query, limit=20)
        requests = [
            {"entity_id": eid, "detail": "state", "timeline": "latest"}
            for eid in entity_ids
        ]
        if not requests:
            return {"requests": [], "relationships": [], "used_chars": 0, "truncated": False}
        return self.retrieve(requests, max_chars=max_chars)

    def search(self, query, top_n=12):
        rows = self.search_metadata(query, limit=top_n)
        return [
            {
                "entity_id": r["id"],
                "source_file": r.get("path"),
                "category": r.get("type"),
                "content": r.get("summary", ""),
                "score": 1.0,
            }
            for r in rows
        ]

    def hybrid_search(self, query, top_n=12):
        result = self.search_metadata_candidates(query, limit=top_n)
        return result["candidates"]
