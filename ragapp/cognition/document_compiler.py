"""Structure-aware semantic compiler for durable canonical cognition.

The compiler extracts *additive* cognition.  Later document segments never
replace earlier entity/event/relationship knowledge.  Each canonical object is
updated in its own file; references are stored on related objects rather than
copying the same description into multiple files.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ragapp.cognition.document_index import DocumentIndex
from ragapp.document_parser import parse_document
from ragapp.settings import MAX_CONTEXT_CHARS, COMPILER_MODEL, CHAT_MODEL, DEFAULT_CHAT_MODEL
from ragapp.config import resolve_model
from ragapp.core.metadata_sync import sync_store
from ragapp.core.retrieval import RetrievalStore, CANONICAL_KINDS

SCHEMA = r'''
You are the semantic compiler for a persistent cognition system.

Your output is NOT a one-line summary. It is durable knowledge that will be
used later without reopening the source document. Extract all materially useful
information present in this segment and make every contribution additive.

Return ONLY valid JSON with this shape:
{
  "project_type": "fiction|business|research|software|other",
  "entities": {
    "stable_entity_id": {
      "type": "character|organization|object|artifact|rule|other",
      "name": "",
      "description_addition": "Detailed facts and characterization newly supported by this segment. Do not replace or summarize away earlier knowledge.",
      "tags": [],
      "knowledge_additions": {
        "identity": [],
        "background": [],
        "appearance": [],
        "personality": [],
        "behavior": [],
        "motivations": [],
        "capabilities": [],
        "preferences": [],
        "other": []
      },
      "attributes": {
        "field": {
          "value": "",
          "summary": "why this state matters",
          "timeline": "Chapter/section/version/date label or null",
          "sequence": 1,
          "valid_from": "",
          "valid_to": null,
          "event_id": "event id or null"
        }
      },
      "relationship_ids": [],
      "event_ids": [],
      "location_ids": [],
      "concept_ids": [],
      "definition_ids": [],
      "knowledge_ids": [],
      "source_node_ids": []
    }
  },
  "relationships": [
    {
      "id": "stable_relationship_id",
      "type": "sibling|friend|dependency|association|other",
      "source": "object id",
      "target": "object id",
      "source_kind": "entity|location|concept|event|definition|knowledge",
      "target_kind": "entity|location|concept|event|definition|knowledge",
      "state": "current or newly observed state",
      "description_addition": "Detailed explanation of how the two things are related and how this segment adds to that understanding.",
      "event_ids": [],
      "timeline": "",
      "sequence": 1,
      "source_node_ids": []
    }
  ],
  "events": [
    {
      "id": "stable_event_id",
      "type": "event|decision|change|discovery|conflict|resolution|arrival|departure|other",
      "title": "",
      "description_addition": "Detailed description of what happened and why it matters.",
      "entities": [],
      "location_ids": [],
      "concept_ids": [],
      "timeline": "",
      "sequence": 1,
      "story_time": "source-native story time or null",
      "previous_event_ids": [],
      "next_event_ids": [],
      "state_changes": [
        {
          "entity_id": "",
          "attribute": "",
          "previous_value": null,
          "new_value": null,
          "description": "what changed",
          "source_node_ids": []
        }
      ],
      "source_node_ids": []
    }
  ],
  "locations": [
    {
      "id": "stable_location_id",
      "name": "",
      "type": "place|region|building|other",
      "description_addition": "Durable knowledge about this location.",
      "attributes": {},
      "entity_ids": [],
      "event_ids": [],
      "concept_ids": [],
      "source_node_ids": []
    }
  ],
  "concepts": [
    {
      "id": "stable_concept_id",
      "name": "",
      "type": "concept|rule|mechanism|other",
      "description_addition": "Durable definition and properties of the concept.",
      "related_entity_ids": [],
      "related_event_ids": [],
      "related_concept_ids": [],
      "source_node_ids": []
    }
  ],
  "definitions": [
    {
      "id": "stable_definition_id",
      "term": "",
      "definition_addition": "The durable definition supported by the source.",
      "related_ids": [],
      "source_node_ids": []
    }
  ],
  "knowledge": [
    {
      "id": "stable_knowledge_id",
      "title": "",
      "description_addition": "Cross-object derived knowledge that does not belong inside a single entity/event/relationship.",
      "subject_ids": [],
      "source_node_ids": []
    }
  ]
}

Rules:
1. NEVER replace previous knowledge. Every description_addition is an additive contribution.
2. Do not write a generic one-line summary when detailed source facts are available.
3. Preserve all durable facts that would help a later agent reconstruct the entity,
   event, relationship, location, concept or definition without rereading the source.
4. Reuse KNOWN IDs when the same object is clearly being described again.
5. Do not invent facts, chronology, locations, relationships, motivations or states.
6. Relationships are first-class objects. Do not copy their detailed description into entities.
7. Events are first-class objects. Do not copy their detailed description into entities.
8. Entities contain intrinsic characterization/knowledge plus references to relationships,
   events, locations, concepts and definitions.
9. A source location is provenance, not cognition. Exact locators are attached by the compiler.
10. Distinguish document order from story time. Use sequence for source/document order and
    story_time only when the source supports it.
11. If a later segment adds a new fact about an existing entity, output ONLY the new contribution
    needed to append to that entity. Do not repeat the entire old profile.
12. State changes must be attached to the event that caused/records them whenever possible.
13. For fiction, preserve personality, habits, behavior, motivations, family history, capabilities,
    important relationships, meaningful events, and durable state transitions.
14. For code/business/research documents, apply the same canonical/additive principle to the
    appropriate objects rather than forcing everything into characters.
15. source_node_ids MUST contain only IDs from SOURCE NODES.
16. Return empty arrays when a category has no supported content.
'''


def _provider_generator(store):
    from ragapp.config import get_api_key, load_project_config
    provider = load_project_config(store).get("provider", {}).get("name", "gemini").lower()
    key = get_api_key(store, provider)
    if not key:
        raise RuntimeError(f"No {provider.title()} API key configured. Add it in Settings or set the matching environment variable.")
    model = resolve_model(store, COMPILER_MODEL or CHAT_MODEL, DEFAULT_CHAT_MODEL, provider=provider)
    if provider == "openrouter":
        from ragapp.llm.tool_calling.openrouter import generate_json
    elif provider == "gemini":
        from ragapp.llm.tool_calling.gemini import generate_json
    elif provider == "azure":
        from ragapp.llm.tool_calling.azure_openai import generate_json
    else:
        raise RuntimeError(f"Unsupported cognition compiler provider: {provider}")
    return lambda prompt: generate_json(store, prompt, model=model), provider, model


def _ref_id(value):
    if isinstance(value, dict):
        value = value.get("id")
    return str(value) if value not in (None, "") else None


def _connected_refs(store, seeds, limit=12):
    """Expand canonical references mechanically; no semantic inference occurs here."""
    queue = list(seeds)
    seen = set()
    out = []
    while queue and len(out) < limit:
        kind, ident = queue.pop(0)
        key = (str(kind), str(ident))
        if key in seen or kind not in CANONICAL_KINDS:
            continue
        seen.add(key)
        rec = store._read_entity(ident) if kind == "entity" else (store.event(ident) if kind == "event" else store._read_object(kind, ident))
        if not rec:
            continue
        out.append(key)

        refs = []
        if kind == "entity":
            refs += [("relationship", _ref_id(x)) for x in rec.get("relationships", [])]
            refs += [("event", _ref_id(x)) for x in rec.get("events", [])]
            refs += [("location", _ref_id(x)) for x in rec.get("locations", [])]
            refs += [("concept", _ref_id(x)) for x in rec.get("concepts", [])]
            refs += [("definition", _ref_id(x)) for x in rec.get("definitions", [])]
            refs += [("knowledge", _ref_id(x)) for x in rec.get("knowledge_links", [])]
        elif kind == "relationship":
            for participant in rec.get("participants", []):
                if isinstance(participant, dict) and participant.get("id"):
                    refs.append((str(participant.get("kind") or "entity"), str(participant["id"])))
            refs += [("event", _ref_id(x)) for x in rec.get("event_ids", [])]
        elif kind == "event":
            refs += [("entity", _ref_id(x)) for x in rec.get("entities", [])]
            refs += [("location", _ref_id(x)) for x in rec.get("location_ids", [])]
            refs += [("concept", _ref_id(x)) for x in rec.get("concept_ids", [])]
        elif kind == "location":
            refs += [("entity", _ref_id(x)) for x in rec.get("entity_ids", [])]
            refs += [("event", _ref_id(x)) for x in rec.get("event_ids", [])]
            refs += [("concept", _ref_id(x)) for x in rec.get("concept_ids", [])]
        elif kind == "concept":
            refs += [("entity", _ref_id(x)) for x in rec.get("related_entity_ids", [])]
            refs += [("event", _ref_id(x)) for x in rec.get("related_event_ids", [])]
            refs += [("concept", _ref_id(x)) for x in rec.get("related_concept_ids", [])]
        elif kind in {"definition", "knowledge"}:
            field = "related_ids" if kind == "definition" else "subject_ids"
            for ref in rec.get(field, []):
                rid = _ref_id(ref)
                if rid:
                    # Generic ids are resolved against existing canonical kinds only.
                    for candidate_kind in CANONICAL_KINDS:
                        if candidate_kind == "entity":
                            exists = rid in store.master_metadata().get("entities", {})
                        elif candidate_kind == "event":
                            exists = bool(store.event(rid))
                        else:
                            exists = bool(store._read_object(candidate_kind, rid))
                        if exists:
                            refs.append((candidate_kind, rid))
                            break
        for ref in refs:
            if ref[1] and ref not in seen:
                queue.append(ref)
    return out


def _compiler_context(store, segment_text, max_chars=6500):
    """Return bounded, relationship-aware canonical context for one compiler call."""
    retrieval = RetrievalStore(store)
    try:
        candidates = retrieval.search_metadata_candidates(segment_text, limit=5).get("candidates", [])
    except Exception:
        candidates = []
    seeds = [(str(c.get("kind")), str(c.get("id"))) for c in candidates if c.get("kind") and c.get("id")]
    refs = _connected_refs(store, seeds, limit=12)
    requests = [{"kind": kind, "id": ident, "detail": "section",
                 "sections": ["description", "attributes", "participants", "relationships", "events",
                              "location_ids", "concept_ids", "related_entity_ids", "related_event_ids",
                              "related_concept_ids", "subject_ids", "timeline", "evolution"]}
                for kind, ident in refs]
    if not requests:
        return {"requests": [], "used_chars": 0, "truncated": False}
    return retrieval.retrieve(requests, max_chars=max_chars)


_VERIFY_SCHEMA = r"""
You are verifying a bounded connected slice of canonical cognition produced by one compilation job.
Perform semantic consistency checking. The host code will NOT infer meaning for you.
Return ONLY JSON:
{
  "valid": true,
  "patches": [
    {"kind":"entity|relationship|event|location|concept|definition|knowledge",
     "id":"canonical id",
     "op":"add|replace|remove",
     "path":"/json/pointer/path",
     "value": "required for add/replace"}
  ]
}
Use patches only for genuine contradictions/inconsistencies in the supplied compiled cognition.
Do not rewrite merely for style. Preserve legitimate temporal evolution and provenance.
Never patch /id, /schema_version, /provenance, /created_at or /updated_at.
If cognition is consistent, return {"valid":true,"patches":[]}.
If repairs are required, return valid=false with the minimal patches that make the supplied cognition consistent.
"""


def _json_pointer_parts(path):
    if not isinstance(path, str) or not path.startswith("/"):
        return []
    return [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]


def _apply_verification_patches(store, patches, allowed_refs):
    """Apply verifier-selected JSON patches mechanically to canonical files."""
    forbidden = {"id", "schema_version", "provenance", "created_at", "updated_at"}
    applied = []
    for patch in patches or []:
        if not isinstance(patch, dict):
            continue
        kind, ident, op = str(patch.get("kind") or ""), str(patch.get("id") or ""), str(patch.get("op") or "")
        if (kind, ident) not in allowed_refs or kind not in CANONICAL_KINDS or op not in {"add", "replace", "remove"}:
            continue
        parts = _json_pointer_parts(patch.get("path"))
        if not parts or parts[0] in forbidden:
            continue
        path = store._entity_path(ident) if kind == "entity" else store._object_path(kind, ident)
        rec = store._read_json(path, {}) if path else {}
        if not rec:
            continue
        parent = rec
        ok = True
        for part in parts[:-1]:
            if isinstance(parent, dict) and part in parent:
                parent = parent[part]
            elif isinstance(parent, list) and part.isdigit() and int(part) < len(parent):
                parent = parent[int(part)]
            else:
                ok = False
                break
        if not ok:
            continue
        leaf = parts[-1]
        try:
            if isinstance(parent, dict):
                if op == "remove":
                    if leaf not in parent:
                        continue
                    parent.pop(leaf)
                else:
                    parent[leaf] = patch.get("value")
            elif isinstance(parent, list):
                if leaf == "-" and op == "add":
                    parent.append(patch.get("value"))
                elif leaf.isdigit():
                    idx = int(leaf)
                    if op == "add" and idx <= len(parent):
                        parent.insert(idx, patch.get("value"))
                    elif op == "replace" and idx < len(parent):
                        parent[idx] = patch.get("value")
                    elif op == "remove" and idx < len(parent):
                        parent.pop(idx)
                    else:
                        continue
                else:
                    continue
            else:
                continue
        except (IndexError, TypeError, ValueError):
            continue
        from ragapp.cognition.store import now_iso
        rec["updated_at"] = now_iso()
        store._write_json(path, rec)
        applied.append({"kind": kind, "id": ident, "op": op, "path": patch.get("path")})
    return applied


def _verify_compilation(store, generator, touched_refs, max_chars=7000):
    """One bounded semantic reconciliation pass for a multi-segment compile job."""
    connected = _connected_refs(store, list(touched_refs), limit=16)
    if len(connected) < 2:
        return {"performed": False, "reason": "insufficient_connected_cognition", "patches_applied": []}
    retrieval = RetrievalStore(store)
    requests = [{"kind": k, "id": i, "detail": "full"} for k, i in connected]
    snapshot = retrieval.retrieve(requests, max_chars=max_chars)
    prompt = _VERIFY_SCHEMA + "\n\nCOMPILED CONNECTED COGNITION:\n" + json.dumps(snapshot, ensure_ascii=False)
    try:
        result = json.loads(generator(prompt))
    except Exception as exc:
        return {"performed": True, "error": str(exc), "patches_applied": []}
    patches = result.get("patches", []) if isinstance(result, dict) else []
    applied = _apply_verification_patches(store, patches, set(connected))
    reverified = None
    if applied:
        # A second API call is spent only when the first verifier actually found
        # and repaired a semantic conflict. Normal compilations stop after one
        # bounded verification call.
        repaired_snapshot = retrieval.retrieve(requests, max_chars=max_chars)
        retry_prompt = _VERIFY_SCHEMA + "\n\nREPAIRED CONNECTED COGNITION; verify the repair:\n" + json.dumps(repaired_snapshot, ensure_ascii=False)
        try:
            retry = json.loads(generator(retry_prompt))
            retry_patches = retry.get("patches", []) if isinstance(retry, dict) else []
            retry_applied = _apply_verification_patches(store, retry_patches, set(connected))
            reverified = {"valid": bool(retry.get("valid")) if isinstance(retry, dict) else False,
                          "patches_requested": len(retry_patches), "patches_applied": retry_applied}
        except Exception as exc:
            reverified = {"valid": False, "error": str(exc), "patches_applied": []}
    return {"performed": True, "valid_before_repair": bool(result.get("valid")) if isinstance(result, dict) else False,
            "patches_requested": len(patches), "patches_applied": applied, "reverification": reverified}


def _valid_evidence(ids, allowed):
    return [x for x in (ids or []) if isinstance(x, str) and x in allowed]


def _location_for(segment, parsed, evidence_ids):
    ids = _valid_evidence(evidence_ids, set(segment.node_ids)) or list(segment.node_ids)
    nodes = [parsed.nodes[x] for x in ids if x in parsed.nodes]
    loc = dict(segment.locator or {})
    if nodes:
        first, last = nodes[0], nodes[-1]
        loc = dict(first.locator or {})
        for k, v in (last.locator or {}).items():
            if k in {"page", "paragraph_index", "slide", "row"}:
                loc[f"{k}_end"] = v
            elif k.endswith("_end"):
                loc[k] = v
        if "paragraph_index" in loc:
            loc.setdefault("paragraph_start", loc.get("paragraph_index"))
            loc.setdefault("paragraph_end", loc.get("paragraph_index_end", loc.get("paragraph_index")))
        if "page" in loc:
            loc.setdefault("page_start", loc.get("page"))
            loc.setdefault("page_end", loc.get("page_end", loc.get("page")))
        if "slide" in loc:
            loc.setdefault("slide_start", loc.get("slide"))
            loc.setdefault("slide_end", loc.get("slide_end", loc.get("slide")))
        if "row" in loc:
            loc.setdefault("row_start", loc.get("row"))
            loc.setdefault("row_end", loc.get("row_end", loc.get("row")))
    loc["node_ids"] = ids
    return loc


def _provenance(segment, parsed, artifact_id, evidence_ids):
    return {
        "artifact_id": artifact_id,
        "segment_id": segment.id,
        "node_ids": _valid_evidence(evidence_ids, set(segment.node_ids)) or list(segment.node_ids),
        "locator": _location_for(segment, parsed, evidence_ids),
        "section_ids": list(segment.section_ids),
        "chapter_id": segment.chapter_id,
        "scene_id": segment.scene_id,
    }


class DocumentCognitionCompiler:
    def __init__(self, store):
        self.store = store
        self.index = DocumentIndex(store)

    def compile_files(self, files: Iterable[tuple[str, Path]], progress_callback=None, authoritative_changes=None) -> dict:
        files = list(files)
        authoritative_changes = [str(x).strip() for x in (authoritative_changes or []) if str(x).strip()]
        generator, provider, model = _provider_generator(self.store)
        parsed_docs = []
        failures = []
        per_segment_budget = max(5000, int(MAX_CONTEXT_CHARS * 0.48))

        for area, path in files:
            rel = path.relative_to(self.store.source if area == "source" else self.store.workspace).as_posix()
            try:
                self.store.register_artifact(area, rel, artifact_type=path.suffix.lower().lstrip(".") or "file")
                parsed = parse_document(path, area=area, max_chars=per_segment_budget)
                parsed.path = rel
                parsed.artifact_id = f"{area}:{rel}"
                self.index.replace_document(parsed)
                parsed_docs.append(parsed)
            except Exception as exc:
                failures.append({"area": area, "file": rel, "error": str(exc)})

        if any(f.get("area") == "source" for f in failures):
            raise RuntimeError("Document parsing failed: " + json.dumps(failures, ensure_ascii=False))

        segments = [(doc, seg) for doc in parsed_docs for seg in doc.segments]
        skipped = []
        processed = 0
        touched_refs = set()

        for i, (doc, seg) in enumerate(segments, 1):
            structure = {
                "artifact_id": doc.artifact_id,
                "document": doc.title,
                "format": doc.format,
                "segment_id": seg.id,
                "chapter_id": seg.chapter_id,
                "scene_id": seg.scene_id,
                "section_ids": seg.section_ids,
                "locator": seg.locator,
                "nodes": [
                    {"id": n.id, "type": n.type, "title": n.title, "locator": n.locator,
                     "attributes": {k: v for k, v in n.attributes.items() if k != "runs"}}
                    for nid in seg.node_ids if (n := doc.nodes.get(nid)) is not None
                ],
            }
            known = _compiler_context(self.store, seg.text, max_chars=min(6500, max(2500, int(MAX_CONTEXT_CHARS * 0.18))))
            override_block = ""
            if authoritative_changes:
                override_block = (
                    "\n\nAUTHORITATIVE CURRENT-STATE OVERRIDES (user-approved; these outrank conflicting older source assertions):\n"
                    + json.dumps(authoritative_changes, ensure_ascii=False)
                    + "\nTreat the source as historical evidence where it conflicts with these overrides. "
                      "Recompile all semantic consequences in this segment against the overrides: attributes, summaries, "
                      "relationships, comparative/derived claims, and connected cognition. Do not re-emit a stale source "
                      "claim as current state merely because it appears in the source text.\n"
                )
            prompt = (
                SCHEMA
                + "\n\nRELATED EXISTING COGNITION (bounded canonical context):\n"
                + json.dumps(known, ensure_ascii=False)
                + override_block
                + "\n\nIMPORTANT: compile this segment against the related cognition above. "
                  "Do not treat it as an isolated extraction. Reuse existing IDs, account for connected "
                  "relationships/events/state, and do not emit a new contribution that contradicts the "
                  "connected cognition unless the source clearly represents a genuine temporal/state change. "
                  "Semantic reconciliation is your responsibility; the host code does not infer meaning.\n"
                + "\n\nSTRUCTURE CONTEXT:\n"
                + json.dumps(structure, ensure_ascii=False)
                + "\n\nSOURCE NODES (the only source text supplied for this call):\n"
                + seg.text
            )
            try:
                data = json.loads(generator(prompt))
            except Exception as exc:
                try:
                    data = json.loads(generator(prompt + "\n\nReturn complete valid JSON only. Preserve every useful fact and use additive fields exactly as specified."))
                except Exception:
                    skipped.append({"segment": seg.id, "error": str(exc)})
                    if progress_callback:
                        progress_callback(i, len(segments))
                    continue

            if not isinstance(data, dict):
                skipped.append({"segment": seg.id, "error": "non_object_json"})
                if progress_callback:
                    progress_callback(i, len(segments))
                continue

            artifact_id = doc.artifact_id
            timeline_label = (seg.title or doc.title or "document")
            timeline_sequence = i
            self.store.record_compilation_chunk(artifact_id, i, locator=json.dumps(seg.locator, ensure_ascii=False), summary=seg.title or "")

            # ----------------------------------------------------------
            # ENTITIES: append intrinsic knowledge; store only references to
            # relationships/events/locations/concepts.
            # ----------------------------------------------------------
            for sid, item in (data.get("entities") or {}).items():
                if not isinstance(item, dict):
                    continue
                eid = str(item.get("id") or sid)
                prov = _provenance(seg, doc, artifact_id, item.get("source_node_ids"))
                update = {
                    "id": eid,
                    "type": item.get("type", "other"),
                    "name": item.get("name") or eid,
                    "description": item.get("description_addition") or item.get("description") or item.get("summary") or "",
                    "summary": (item.get("description_addition") or item.get("summary") or "").strip().split("\n", 1)[0][:500],
                    "tags": item.get("tags", []),
                    "knowledge": item.get("knowledge_additions", {}),
                    "attributes": item.get("attributes", {}),
                    "relationships": [{"id": x} for x in item.get("relationship_ids", []) if x],
                    "events": [{"id": x} for x in item.get("event_ids", []) if x],
                    "locations": [{"id": x} for x in item.get("location_ids", []) if x],
                    "concepts": [{"id": x} for x in item.get("concept_ids", []) if x],
                    "definitions": [{"id": x} for x in item.get("definition_ids", []) if x],
                    "knowledge_links": [{"id": x} for x in item.get("knowledge_ids", []) if x],
                    "provenance": [prov],
                    "timeline": {"label": timeline_label, "sequence": timeline_sequence, "story_time": None},
                }
                self.store.upsert_entity_update(update, source_artifact=artifact_id, chunk_index=i, default_timeline=timeline_label, default_sequence=timeline_sequence)
                touched_refs.add(("entity", eid))
                self.index.attach("entity", eid, artifact_id, node_ids=prov["node_ids"], segment_id=seg.id, locator=prov["locator"], section_ids=prov["section_ids"], chapter_id=prov["chapter_id"], scene_id=prov["scene_id"])

            # ----------------------------------------------------------
            # LOCATIONS / CONCEPTS / DEFINITIONS / CROSS-OBJECT KNOWLEDGE
            # ----------------------------------------------------------
            for item in data.get("locations") or []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                prov = _provenance(seg, doc, artifact_id, item.get("source_node_ids"))
                rec = {
                    "id": str(item["id"]), "name": item.get("name") or item["id"], "type": item.get("type", "place"),
                    "description": item.get("description_addition") or item.get("description") or "",
                    "attributes": item.get("attributes", {}),
                    "entity_ids": list(item.get("entity_ids") or []), "event_ids": list(item.get("event_ids") or []),
                    "concept_ids": list(item.get("concept_ids") or []), "source_locations": [prov],
                }
                stored = self.store.upsert_canonical("location", rec, source_artifact=artifact_id, timeline=timeline_label, location=prov["locator"])
                touched_refs.add(("location", str(stored["id"])))
                for eid in rec.get("entity_ids", []):
                    if str(eid) in self.store.master_metadata().get("entities", {}):
                        self.store._link_reference("entity", str(eid), "locations", stored["id"])
                        self.store._link_object_reference("location", stored["id"], "entity_ids", str(eid), as_object=False)
                self.index.attach("location", stored["id"], artifact_id, node_ids=prov["node_ids"], segment_id=seg.id, locator=prov["locator"], section_ids=prov["section_ids"], chapter_id=prov["chapter_id"], scene_id=prov["scene_id"])

            for item in data.get("concepts") or []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                prov = _provenance(seg, doc, artifact_id, item.get("source_node_ids"))
                rec = {"id": str(item["id"]), "name": item.get("name") or item["id"], "type": item.get("type", "concept"), "description": item.get("description_addition") or item.get("description") or "", "related_entity_ids": item.get("related_entity_ids", []), "related_event_ids": item.get("related_event_ids", []), "related_concept_ids": item.get("related_concept_ids", []), "source_locations": [prov]}
                stored = self.store.upsert_canonical("concept", rec, source_artifact=artifact_id, timeline=timeline_label, location=prov["locator"])
                touched_refs.add(("concept", str(stored["id"])))
                for eid in rec.get("related_entity_ids", []):
                    if str(eid) in self.store.master_metadata().get("entities", {}):
                        self.store._link_reference("entity", str(eid), "concepts", stored["id"])
                self.index.attach("concept", stored["id"], artifact_id, node_ids=prov["node_ids"], segment_id=seg.id, locator=prov["locator"], section_ids=prov["section_ids"], chapter_id=prov["chapter_id"], scene_id=prov["scene_id"])

            for item in data.get("definitions") or []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                prov = _provenance(seg, doc, artifact_id, item.get("source_node_ids"))
                rec = {"id": str(item["id"]), "term": item.get("term") or item["id"], "description": item.get("definition_addition") or item.get("definition") or "", "related_ids": item.get("related_ids", []), "source_locations": [prov]}
                stored = self.store.upsert_canonical("definition", rec, source_artifact=artifact_id, timeline=timeline_label, location=prov["locator"])
                touched_refs.add(("definition", str(stored["id"])))
                self.index.attach("definition", stored["id"], artifact_id, node_ids=prov["node_ids"], segment_id=seg.id, locator=prov["locator"], section_ids=prov["section_ids"], chapter_id=prov["chapter_id"], scene_id=prov["scene_id"])

            for item in data.get("knowledge") or []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                prov = _provenance(seg, doc, artifact_id, item.get("source_node_ids"))
                rec = {"id": str(item["id"]), "title": item.get("title") or item["id"], "description": item.get("description_addition") or item.get("description") or "", "subject_ids": item.get("subject_ids", []), "source_locations": [prov]}
                stored = self.store.upsert_canonical("knowledge", rec, source_artifact=artifact_id, timeline=timeline_label, location=prov["locator"])
                touched_refs.add(("knowledge", str(stored["id"])))
                self.index.attach("knowledge", stored["id"], artifact_id, node_ids=prov["node_ids"], segment_id=seg.id, locator=prov["locator"], section_ids=prov["section_ids"], chapter_id=prov["chapter_id"], scene_id=prov["scene_id"])

            # ----------------------------------------------------------
            # RELATIONSHIPS: one canonical file; both entities only reference it.
            # ----------------------------------------------------------
            for item in data.get("relationships") or []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                prov = _provenance(seg, doc, artifact_id, item.get("source_node_ids"))
                rel = {
                    "id": str(item["id"]), "type": item.get("type", "related_to"), "source": item.get("source"), "target": item.get("target"),
                    "source_kind": item.get("source_kind", "entity"), "target_kind": item.get("target_kind", "entity"),
                    "state": item.get("state"), "description": item.get("description_addition") or item.get("description") or "",
                    "timeline": item.get("timeline") or timeline_label, "sequence": item.get("sequence") or timeline_sequence,
                    "event_ids": item.get("event_ids", []), "document_locations": [prov],
                }
                self.store.add_relationship(rel, source_artifact=artifact_id, timeline=rel["timeline"])
                touched_refs.add(("relationship", rel["id"]))
                self.index.attach("relationship", rel["id"], artifact_id, node_ids=prov["node_ids"], segment_id=seg.id, locator=prov["locator"], section_ids=prov["section_ids"], chapter_id=prov["chapter_id"], scene_id=prov["scene_id"])

            # ----------------------------------------------------------
            # EVENTS: one canonical file with additive evolution and temporal
            # links. Entity files get references only.
            # ----------------------------------------------------------
            for item in data.get("events") or []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                prov = _provenance(seg, doc, artifact_id, item.get("source_node_ids"))
                pos = {"label": item.get("timeline") or timeline_label, "sequence": item.get("sequence") or timeline_sequence, "story_time": item.get("story_time")}
                event = {
                    "id": str(item["id"]), "type": item.get("type", "event"), "title": item.get("title") or item["id"],
                    "description": item.get("description_addition") or item.get("description") or "", "entities": item.get("entities", []),
                    "location_ids": item.get("location_ids", []), "concept_ids": item.get("concept_ids", []), "narrative_position": pos,
                    "sequence": pos["sequence"], "previous_events": item.get("previous_event_ids", []), "next_events": item.get("next_event_ids", []),
                    "state_changes": item.get("state_changes", []), "source_locator": prov["locator"], "document_locations": [prov],
                }
                event_id = self.store.add_event(event, source_artifact=artifact_id, timeline=pos["label"], sequence=pos["sequence"], narrative_position=pos)
                touched_refs.add(("event", str(event_id)))
                self.index.attach("event", event_id, artifact_id, node_ids=prov["node_ids"], segment_id=seg.id, locator=prov["locator"], section_ids=prov["section_ids"], chapter_id=prov["chapter_id"], scene_id=prov["scene_id"])

                for change in item.get("state_changes") or []:
                    if not isinstance(change, dict) or not change.get("entity_id") or not change.get("attribute"):
                        continue
                    state_prov = _provenance(seg, doc, artifact_id, change.get("source_node_ids"))
                    self.store.record_state_change(
                        entity_id=str(change["entity_id"]),
                        attribute=str(change["attribute"]),
                        previous_value=change.get("previous_value"),
                        new_value=change.get("new_value"),
                        description=change.get("description") or "",
                        event_id=event_id,
                        timeline=pos["label"], sequence=pos["sequence"],
                        source_artifact=artifact_id, locator=state_prov["locator"],
                    )

            if data.get("project_type"):
                self.store.set_project_type(data["project_type"])
            processed += 1
            if progress_callback:
                progress_callback(i, len(segments))

        verification = {"performed": False, "reason": "single_segment_or_no_cognition", "patches_applied": []}
        if processed > 0 and len(touched_refs) > 1:
            verification = _verify_compilation(
                self.store, generator, touched_refs,
                max_chars=min(7000, max(3000, int(MAX_CONTEXT_CHARS * 0.20))),
            )

        self.store.mark_compiled()
        sync_store(self.store)
        return {
            "status": "compiled",
            "provider": provider,
            "model": model,
            "source_files": sum(1 for a, _ in files if a == "source"),
            "workspace_files": sum(1 for a, _ in files if a == "workspace"),
            "documents": len(parsed_docs),
            "segments": len(segments),
            "processed_segments": processed,
            "failed_files": failures,
            "skipped_segments": skipped,
            "entities": len(self.store.master_metadata().get("entities", {})),
            "relationships": len(self.store.relationships()),
            "events": len(self.store.events()),
            "locations": len(self.store._all_kind_records("location")),
            "concepts": len(self.store._all_kind_records("concept")),
            "definitions": len(self.store._all_kind_records("definition")),
            "knowledge": len(self.store._all_kind_records("knowledge")),
            "verification": verification,
        }
