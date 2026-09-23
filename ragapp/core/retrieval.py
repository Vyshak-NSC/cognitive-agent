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
            "current_state": self._current_state(entity),
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

    def _current_state(self, entity):
        """Return the canonical current-value projection for retrieval.

        Durable chat/agent state takes precedence over later source reingestion;
        the entity file remains authoritative and this is only a response view.
        """
        out = {}
        for name in (entity.get("attributes") or {}):
            latest = self.store.latest_attribute_state(entity.get("id"), name)
            if not isinstance(latest, dict):
                continue
            out[str(name)] = {
                "value": latest.get("value"),
                "summary": latest.get("summary", ""),
                "valid_from": latest.get("valid_from"),
                "valid_to": latest.get("valid_to"),
                "event_id": latest.get("event_id"),
            }
        return out

    @staticmethod
    def _json_size(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    def _chunk_item(self, item, max_chars):
        """Split one oversized cognition result into valid structured chunks."""
        max_chars = max(1000, int(max_chars or 12000))
        base = {k: item[k] for k in ("id", "type", "name", "summary", "current_state") if k in item}
        parts = []
        current = dict(base)
        reserve = self._json_size({"chunk": {"index": 999, "count": 999}}) + 64

        def flush():
            nonlocal current
            if len(current) > len(base):
                parts.append(current)
            current = dict(base)

        def add_value(key, value):
            nonlocal current
            candidate = dict(current)
            candidate[key] = value
            if self._json_size(candidate) + reserve <= max_chars:
                current = candidate
                return

            if key in current:
                flush()

            if self._json_size({**base, key: value}) + reserve <= max_chars:
                current[key] = value
                return

            if isinstance(value, str):
                text = value
                # Keep valid UTF-8/JSON boundaries and leave room for wrapper metadata.
                step = max(256, max_chars - self._json_size(base) - reserve - len(key) - 64)
                for start in range(0, len(text), step):
                    if len(current) > len(base):
                        flush()
                    current[key] = text[start:start + step]
                    flush()
                return

            if isinstance(value, list):
                bucket = []
                for entry in value:
                    candidate_list = bucket + [entry]
                    candidate = dict(base)
                    candidate[key] = candidate_list
                    if self._json_size(candidate) + reserve <= max_chars:
                        bucket = candidate_list
                    else:
                        if bucket:
                            current[key] = bucket
                            flush()
                        bucket = [entry]
                if bucket:
                    current[key] = bucket
                    flush()
                return

            if isinstance(value, dict):
                bucket = {}
                for subkey, entry in value.items():
                    candidate_map = dict(bucket)
                    candidate_map[subkey] = entry
                    candidate = dict(base)
                    candidate[key] = candidate_map
                    if self._json_size(candidate) + reserve <= max_chars:
                        bucket = candidate_map
                    else:
                        if bucket:
                            current[key] = bucket
                            flush()
                        bucket = {subkey: entry}
                if bucket:
                    current[key] = bucket
                    flush()
                return

            text = str(value)
            step = max(256, max_chars - self._json_size(base) - reserve - len(key) - 64)
            for start in range(0, len(text), step):
                current[key] = text[start:start + step]
                flush()

        for key, value in item.items():
            if key in base:
                continue
            add_value(key, value)
        flush()

        if not parts:
            parts = [base]

        total = len(parts)
        out = []
        for index, data in enumerate(parts, 1):
            chunk = dict(data)
            chunk["chunk"] = {"index": index, "count": total, "of_entity": item.get("id")}
            out.append(chunk)
        return out

    def _fetch_request(self, req):
        """Resolve one retrieval request directly from canonical cognition."""
        detail = str(req.get("detail", "summary")).lower()

        # Event-targeted retrieval.
        event_ids = []
        if req.get("event_id"):
            event_ids.append(str(req["event_id"]))
        event_ids.extend(str(x) for x in (req.get("event_ids") or []) if x)
        if event_ids:
            out = []
            for event_id in dict.fromkeys(event_ids):
                event = self.store.event(event_id)
                if event:
                    out.append(dict(event))
            return out

        if req.get("event_query"):
            q = str(req["event_query"]).lower()
            out = []
            for event in self.store.events(limit=1000):
                hay = json.dumps(event, ensure_ascii=False).lower()
                if q in hay:
                    out.append(dict(event))
            return out

        entity_ids = []
        if req.get("entity_id"):
            entity_ids.append(str(req["entity_id"]))
        entity_ids.extend(str(x) for x in (req.get("entity_ids") or []) if x)
        if req.get("name") and not entity_ids:
            entity_ids.extend(self.store.matching_entities(str(req["name"])))
        if req.get("query") and not entity_ids:
            entity_ids.extend(self._rank_candidates(str(req["query"]), limit=8))

        out = []
        for eid in dict.fromkeys(entity_ids):
            entity = self.store._read_entity(eid)
            if not entity:
                continue
            if detail == "metadata":
                meta = dict(self.store.master_metadata().get("entities", {}).get(eid) or {})
                meta["id"] = eid
                out.append(meta)
                continue

            if detail == "summary":
                out.append({
                    "id": eid,
                    "type": entity.get("type"),
                    "name": entity.get("name"),
                    "summary": entity.get("summary", ""),
                    "current_state": self._current_state(entity),
                })
                continue

            if detail == "state":
                requested_attributes = [str(x) for x in (req.get("attributes") or [])]
                state = self._current_state(entity)
                if requested_attributes:
                    state = {k: v for k, v in state.items() if k in requested_attributes}
                out.append({
                    "id": eid,
                    "type": entity.get("type"),
                    "name": entity.get("name"),
                    "summary": entity.get("summary", ""),
                    "current_state": state,
                    "timeline": entity.get("timeline", []),
                })
                continue

            if detail == "section":
                sections = [str(x) for x in (req.get("sections") or [])]
                item = {"id": eid, "type": entity.get("type"), "name": entity.get("name")}
                if not sections or "summary" in sections or "identity" in sections:
                    item["summary"] = entity.get("summary", "")
                    item["description"] = entity.get("description", "")
                if "state" in sections or "attributes" in sections or not sections:
                    item["current_state"] = self._current_state(entity)
                if "relationships" in sections or not sections:
                    item["relationships"] = entity.get("relationships", [])
                if "events" in sections or not sections:
                    item["events"] = entity.get("events", [])
                if "timeline" in sections or not sections:
                    item["timeline"] = entity.get("timeline", [])
                out.append(item)
                continue

            # full: return the canonical entity object, not metadata/source.
            item = dict(entity)
            item["current_state"] = self._current_state(entity)
            out.append(item)

        return out

    def retrieve(self, requests, max_chars=12000):
        """Retrieve canonical cognition without silently dropping oversized objects.

        Oversized ``full`` requests are deterministically chunked. The caller
        requests the next chunk by adding ``chunk_index`` to the same request.
        This keeps each tool result bounded while guaranteeing that a large
        entity never becomes an empty retrieval result.
        """
        max_chars = max(1000, int(max_chars or 12000))
        result = {"requests": [], "used_chars": 0, "truncated": False}
        next_requests = []
        used = 0

        for req in requests or []:
            if not isinstance(req, dict):
                continue
            detail = str(req.get("detail", "summary")).lower()
            requested_chunk = max(0, int(req.get("chunk_index", 0) or 0))
            items = self._fetch_request(req)
            if not items:
                continue

            for item in items:
                chunks = self._chunk_item(item, max_chars) if detail == "full" else [item]

                if detail == "full":
                    if requested_chunk >= len(chunks):
                        requested_chunk = 0
                    chunk = chunks[requested_chunk]
                    payload_size = self._json_size(chunk)
                    if payload_size > max_chars:
                        minimal = {
                            k: chunk[k]
                            for k in ("id", "type", "name", "summary", "current_state")
                            if k in chunk
                        }
                        minimal["chunk"] = {
                            "index": requested_chunk + 1,
                            "count": len(chunks),
                            "of_entity": chunk.get("id"),
                            "oversized": True,
                        }
                        chunk = minimal
                        payload_size = self._json_size(chunk)

                    if used + payload_size > max_chars and result["requests"]:
                        result["truncated"] = True
                        break
                    result["requests"].append(chunk)
                    used += payload_size
                    if requested_chunk + 1 < len(chunks):
                        continuation = dict(req)
                        continuation["chunk_index"] = requested_chunk + 1
                        next_requests.append(continuation)
                    break

                payload_size = self._json_size(item)
                if used + payload_size > max_chars and result["requests"]:
                    result["truncated"] = True
                    break
                result["requests"].append(item)
                used += payload_size

            if result["truncated"]:
                break

        result["used_chars"] = used
        if next_requests:
            result["next_requests"] = next_requests
            result["truncated"] = True
        return result

    # Legacy methods retained for compatibility. They are no longer used as
    # eager context injection by the agent loop.
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
