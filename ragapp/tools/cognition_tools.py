from __future__ import annotations

from ragapp.tools.definitions import Tool


def _section_cognition(store, section_id, artifact_id=None):
    section = store.document_index.section(section_id)
    if not section:
        return {"found": False, "section_id": section_id}
    result = {"found": True, "section": section, "entities": {}, "events": {}, "relationships": {}}
    data = store.document_index._read()
    for kind, key in (("entity", "entities"), ("event", "events"), ("relationship", "relationships"), ("location", "locations"), ("concept", "concepts")):
        bucket = data.get(f"{kind}_locations", {})
        for ident, locations in bucket.items():
            if any((not artifact_id or loc.get("artifact_id") == artifact_id) and (section_id in (loc.get("section_ids") or []) or loc.get("chapter_id") == section_id or loc.get("scene_id") == section_id) for loc in locations):
                if kind == "entity":
                    obj = store.entity(ident)
                elif kind == "event":
                    obj = store.event(ident)
                elif kind == "relationship":
                    obj = store.relationships().get(ident)
                else:
                    obj = store._read_object(kind, ident)
                if obj:
                    result.setdefault(f"{kind}s", {})[ident] = obj
    return result


def build_cognition_tools(store, session_id=None):
    return [
        Tool(
            "search_cognition_metadata",
            "Search canonical persisted cognition and document structure without loading source text. Returns compact candidate IDs, kinds, names, summaries and provenance.",
            {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
            lambda query, limit=8: store.search_cognition_metadata(query, limit=limit),
        ),
        Tool(
            "get_cognition_object",
            "Load one canonical cognition object by kind and ID. This returns derived cognition, not raw source text.",
            {"type": "object", "properties": {"kind": {"type": "string", "enum": ["entity", "relationship", "event", "location", "concept", "definition", "knowledge"]}, "id": {"type": "string"}}, "required": ["kind", "id"]},
            lambda kind, id: store.entity(id) if kind == "entity" else (store.event(id) if kind == "event" else store.relationships().get(id) if kind == "relationship" else store._read_object(kind, id)),
        ),
        Tool(
            "get_relationship_history",
            "Return the evolution of one canonical relationship without reading the source document.",
            {"type": "object", "properties": {"relationship_id": {"type": "string"}}, "required": ["relationship_id"]},
            lambda relationship_id: store.relationship_history(relationship_id),
        ),
        Tool(
            "get_document_structure",
            "Return document/chapter/section metadata and locators only.",
            {"type": "object", "properties": {"artifact_id": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer"}}},
            lambda artifact_id=None, query=None, limit=200: store.document_structure(artifact_id=artifact_id, query=query, limit=limit),
        ),
        Tool(
            "resolve_document_section",
            "Resolve a chapter/section name to its exact structural locator without loading source text.",
            {"type": "object", "properties": {"query": {"type": "string"}, "artifact_id": {"type": "string"}}, "required": ["query"]},
            lambda query, artifact_id=None: store.resolve_document_section(query, artifact_id=artifact_id),
        ),
        Tool(
            "get_section_cognition",
            "Return canonical cognition associated with a document section without loading its source body.",
            {"type": "object", "properties": {"section_id": {"type": "string"}, "artifact_id": {"type": "string"}}, "required": ["section_id"]},
            lambda section_id, artifact_id=None: _section_cognition(store, section_id, artifact_id),
        ),
        Tool(
            "get_cognition_locations",
            "Return source locators for a canonical cognition object.",
            {"type": "object", "properties": {"kind": {"type": "string"}, "id": {"type": "string"}}, "required": ["kind", "id"]},
            lambda kind, id: store.cognition_locations(kind, id),
        ),
        Tool(
            "request_cognition_context",
            "Retrieve targeted canonical cognition. This never returns raw source content.",
            {"type": "object", "properties": {"requests": {"type": "array", "items": {"type": "object"}}, "max_chars": {"type": "integer"}}, "required": ["requests"]},
            lambda requests, max_chars=16000: store.retrieve(requests, max_chars=max_chars),
        ),
        Tool(
            "deliver_source_to_user",
            "Read and return exact source evidence directly to the user. Use only when the user explicitly asks to see/show the source content; do not use this to obtain source text for reasoning.",
            {"type": "object", "properties": {"artifact_id": {"type": "string"}, "locator": {"type": "object"}}, "required": ["artifact_id", "locator"]},
            lambda artifact_id, locator: store.read_source_location(artifact_id, locator),
        ),
        Tool(
            "read_source_location",
            "Read exact source evidence only after a canonical cognition object or document structure has identified the locator.",
            {"type": "object", "properties": {"artifact_id": {"type": "string"}, "locator": {"type": "object"}}, "required": ["artifact_id", "locator"]},
            lambda artifact_id, locator: store.read_source_location(artifact_id, locator),
        ),
    ]
