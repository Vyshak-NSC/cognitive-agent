import json
from ragapp.tools.definitions import Tool
from ragapp.cognition.session_memory import SessionMemory
from ragapp.core.retrieval import RetrievalStore


def _entity_metadata_with_current_state(store, names):
    """Return metadata plus the latest durable state from canonical entity files.

    master_metadata is a compact search/index projection and can legitimately
    retain the source-derived summary. Current durable state must come from the
    canonical entity object so a STATE_UPDATE is visible in later sessions.
    """
    master = store.master_metadata().get("entities", {})
    out = []
    for name in names or []:
        key = str(name)
        eid = key if key in master else None
        if eid is None:
            matches = store.matching_entities(key)
            eid = matches[0] if matches else None
        if not eid:
            out.append(None)
            continue
        meta = dict(master.get(eid) or {})
        entity = store._read_entity(eid)
        if entity:
            current = {}
            for attr in (entity.get("attributes") or {}):
                latest = store.latest_attribute_state(eid, attr)
                if isinstance(latest, dict):
                    current[str(attr)] = {
                        "value": latest.get("value"),
                        "summary": latest.get("summary", ""),
                        "valid_from": latest.get("valid_from"),
                        "valid_to": latest.get("valid_to"),
                        "event_id": latest.get("event_id"),
                    }
            meta["current_state"] = current
            meta["canonical_updated_at"] = entity.get("updated_at")
        out.append(meta)
    return {"entities": out}

def build_cognition_tools(store, session_id=None):
    retrieval = RetrievalStore(store)

    return [
        Tool(
            "search_cognition_metadata",
            "Search compact canonical metadata across entities, relationships, events, locations, concepts, definitions, and knowledge. The controller performs an initial search automatically for project-content turns; call this tool only to refine or broaden retrieval.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
            lambda query, limit=8: retrieval.search_metadata_candidates(query, limit=limit),
        ),
        Tool(
            "get_cognition_index",
            "Compatibility tool. Do not use for normal retrieval because it may be large. Use search_cognition_metadata instead.",
            {"type": "object", "properties": {}},
            lambda: {
                "project_id": store.project_id,
                "current_version": store.master_metadata().get("current_version", 0),
                "entity_count": len(store.master_metadata().get("entities", {})),
                "artifact_count": len(store.master_metadata().get("artifacts", {})),
                "use": "Call search_cognition_metadata(query) for actual retrieval.",
            },
        ),
        Tool(
            "request_cognition_context",
            "Retrieve targeted canonical cognition. Prefer compact/state/section; request full only when necessary. Continuation is explicit. Request additional pages only when the returned evidence is insufficient.",
            {
                "type": "object",
                "properties": {
                    "requests": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": ["entity", "relationship", "event", "location", "concept", "definition", "knowledge"]},
                                "id": {"type": "string"},
                                "ids": {"type": "array", "items": {"type": "string"}},
                                "entity_id": {"type": "string"},
                                "entity_ids": {"type": "array", "items": {"type": "string"}},
                                "event_id": {"type": "string"},
                                "event_ids": {"type": "array", "items": {"type": "string"}},
                                "event_query": {"type": "string"},
                                "name": {"type": "string"},
                                "query": {"type": "string"},
                                "attributes": {"type": "array", "items": {"type": "string"}},
                                "sections": {"type": "array", "items": {"type": "string"}},
                                "timeline": {"type": "string"},
                                "as_of": {"type": "object"},
                                "story_time": {"type": "object"},
                                "narrative_position": {"type": "object"},
                                "temporal_position": {"type": "object"},
                                "detail": {"type": "string", "enum": ["metadata", "summary", "compact", "state", "section", "full"]},
                                "include_source": {"type": "boolean"},
                                "chunk_index": {"type": "integer", "minimum": 0},
                            },
                        },
                    },
                    "max_chars": {"type": "integer"},
                },
                "required": ["requests"],
            },
            lambda requests, max_chars=4000: retrieval.retrieve(requests, max_chars=max_chars),
        ),
        Tool(
            "get_entity_metadata",
            "Return only metadata for named entities so the agent can decide whether a deeper fetch is necessary.",
            {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}}, "required": ["names"]},
            lambda names: _entity_metadata_with_current_state(store, names),
        ),
        Tool("get_state_map", "Legacy compatibility view. Do not use for retrieval; use search_cognition_metadata and request_cognition_context.", {"type": "object", "properties": {}}, lambda: {"project_id": store.project_id, "entity_count": len(store.master_metadata().get("entities", {})), "use": "search_cognition_metadata"}),
        Tool("get_ledger", "Legacy compatibility view. Do not use for retrieval; use request_cognition_context.", {"type": "object", "properties": {}}, lambda: {"entity_count": len(store.master_metadata().get("entities", {})), "use": "request_cognition_context"}),
        Tool("load_entities", "Load exact entity files and directly linked relationships. Use targeted names only.", {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}}, "required": ["names"]}, lambda names: store.load_entities(names)),
        Tool("record_fact", "Persist a durable fact with provenance/confidence/temporal validity.", {"type": "object", "properties": {"claim": {"type": "string"}, "status": {"type": "string"}, "confidence": {"type": "number"}, "evidence_ids": {"type": "array", "items": {"type": "string"}}, "entities": {"type": "array", "items": {"type": "string"}}, "valid_from": {"type": "string"}, "valid_to": {"type": "string"}, "source": {"type": "string"}}, "required": ["claim"]}, lambda **a: _record_knowledge(store, "fact", a)),
        Tool("record_evidence", "Persist evidence and its source locator.", {"type": "object", "properties": {"claim": {"type": "string"}, "source_file": {"type": "string"}, "locator": {"type": "string"}, "excerpt": {"type": "string"}, "reliability": {"type": "number"}, "source_type": {"type": "string"}, "source": {"type": "string"}}, "required": ["claim"]}, lambda **a: _record_knowledge(store, "evidence", a)),
        Tool("record_hypothesis", "Persist a testable hypothesis and supporting/disconfirming evidence.", {"type": "object", "properties": {"statement": {"type": "string"}, "status": {"type": "string"}, "confidence": {"type": "number"}, "supporting_evidence": {"type": "array", "items": {"type": "string"}}, "disconfirming_evidence": {"type": "array", "items": {"type": "string"}}}, "required": ["statement"]}, lambda **a: _record_knowledge(store, "hypothesis", a)),
        Tool("record_decision", "Persist decision, rationale, alternatives and consequences.", {"type": "object", "properties": {"decision": {"type": "string"}, "rationale": {"type": "string"}, "alternatives": {"type": "array", "items": {"type": "string"}}, "consequences": {"type": "array", "items": {"type": "string"}}, "owner": {"type": "string"}}, "required": ["decision", "rationale"]}, lambda **a: _record_knowledge(store, "decision", a)),
        Tool("record_dependency", "Persist a relationship/dependency between project elements.", {"type": "object", "properties": {"source": {"type": "string"}, "target": {"type": "string"}, "relation": {"type": "string"}, "impact": {"type": "string"}}, "required": ["source", "target", "relation"]}, lambda **a: _record_dependency(store, a)),
        Tool("record_change", "Persist a change and its affected elements/validation.", {"type": "object", "properties": {"description": {"type": "string"}, "affected": {"type": "array", "items": {"type": "string"}}, "caused_by": {"type": "string"}, "validation": {"type": "string"}}, "required": ["description"]}, lambda **a: _record_knowledge(store, "change", a)),
        Tool("get_contradictions", "Find candidate contradictions requiring validation.", {"type": "object", "properties": {}}, lambda: _validate(store)["contradiction_candidates"]),
        Tool("analyze_impact", "Return project elements that may be affected by a change using persisted dependencies.", {"type": "object", "properties": {"element": {"type": "string"}}, "required": ["element"]}, lambda element: _impact(store, element)),
        Tool("validate_cognition", "Check cognition integrity.", {"type": "object", "properties": {}}, lambda: _validate(store)),
        Tool("get_world_model", "Return facts, evidence, hypotheses, decisions, dependencies, changes and open questions.", {"type": "object", "properties": {"query": {"type": "string"}}}, lambda query="": _world_model_snapshot(store, query)),
        Tool("distill_session", "Persist a durable episode from the current conversation.", {"type": "object", "properties": {"transcript": {"type": "array", "items": {"type": "object"}}}, "required": ["transcript"]}, lambda transcript: SessionMemory(store).distill(session_id or "unknown", transcript)),
    ]


def _record_knowledge(store, subtype, data):
    import uuid
    payload = dict(data or {})
    ident = str(payload.pop("id", "") or f"{subtype}:{uuid.uuid4().hex}")
    payload.update({"id": ident, "subtype": subtype})
    store.commit_authoritative_change(f"Pre-state backup before {subtype}")
    result = store.upsert_canonical("knowledge", payload, source_artifact=payload.get("source"))
    store.commit_authoritative_change(f"Record {subtype}")
    return result


def _record_dependency(store, data):
    import uuid
    payload = dict(data or {})
    ident = str(payload.pop("id", "") or f"dependency:{uuid.uuid4().hex}")
    payload.update({"id": ident, "type": "dependency", "relation_type": payload.get("relation") or "depends_on", "participants": [
        {"id": str(payload.get("source")), "kind": "entity", "role": "source"},
        {"id": str(payload.get("target")), "kind": "entity", "role": "target"},
    ]})
    store.commit_authoritative_change("Pre-state backup before dependency")
    result = store.add_relationship(payload)
    store.commit_authoritative_change("Record dependency")
    return result


def _knowledge_snapshot(store):
    grouped = {"facts": {}, "evidence": {}, "hypotheses": {}, "decisions": {}, "changes": {}}
    for rec in store._all_kind_records("knowledge"):
        subtype = str(rec.get("subtype") or "")
        bucket = {"fact": "facts", "evidence": "evidence", "hypothesis": "hypotheses", "decision": "decisions", "change": "changes"}.get(subtype)
        if bucket:
            grouped[bucket][str(rec.get("id"))] = rec
    grouped["dependencies"] = {rid: rel for rid, rel in store.relationships().items() if rel.get("type") == "dependency" or rel.get("relation_type") == "dependency"}
    grouped["contradictions"] = {}
    grouped["open_questions"] = {}
    return grouped


def _world_model_snapshot(store, query=""):
    m = _knowledge_snapshot(store)
    if not query:
        return m
    q = str(query).lower()
    return {k: {i: v for i, v in bucket.items() if q in json.dumps(v, ensure_ascii=False).lower()} if isinstance(bucket, dict) else bucket for k, bucket in m.items()}


def _impact(store, element):
    deps = _knowledge_snapshot(store).get("dependencies", {})
    affected = []
    seen = {element}
    queue = [element]
    while queue:
        cur = queue.pop(0)
        for d in deps.values():
            if d.get("source") == cur and d.get("target") not in seen:
                seen.add(d.get("target")); queue.append(d.get("target")); affected.append(d)
            elif d.get("target") == cur and d.get("source") not in seen:
                seen.add(d.get("source")); queue.append(d.get("source")); affected.append(d)
    return {"element": element, "affected": affected}



def _contradictions(store):
    facts = list(_knowledge_snapshot(store).get("facts", {}).values())
    out = []
    for i, a in enumerate(facts):
        for b in facts[i + 1:]:
            if set(a.get("entities", [])) & set(b.get("entities", [])) and a.get("claim") and b.get("claim"):
                if a.get("status") == "supported" and b.get("status") == "supported" and a.get("claim", "").lower() != b.get("claim", "").lower():
                    out.append({"fact_a": a["id"], "fact_b": b["id"], "reason": "same entities with competing supported claims"})
    return out

def _validate(store):
    m = _knowledge_snapshot(store)
    evidence = set(m.get("evidence", {}))
    errors = []
    for fid, f in m.get("facts", {}).items():
        for eid in f.get("evidence_ids", []):
            if eid not in evidence:
                errors.append({"fact": fid, "missing_evidence": eid})
    for did, d in m.get("decisions", {}).items():
        if not d.get("rationale"):
            errors.append({"decision": did, "error": "missing rationale"})
    return {"valid": not errors, "errors": errors, "contradiction_candidates": _contradictions(store)}
