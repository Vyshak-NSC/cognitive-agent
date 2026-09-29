"""Durable agent-state merge into the canonical cognition model."""
from __future__ import annotations

import json
from ragapp.cognition.store import CognitionStore, now_iso

ALLOWED_PERMANENCE = {"permanent", "transient"}
CANONICAL_KINDS = {"entity", "relationship", "event", "location", "concept", "definition", "knowledge"}


def _semantic_generator(store):
    from ragapp.cognition.document_compiler import _provider_generator
    generator, _provider, _model = _provider_generator(store)
    return generator


def _named_entity_seeds(store, request):
    """Entities whose id/name literally appears in the request (structural, no LLM).

    Lexical/semantic search alone can miss short names, so the entities the user
    actually named are always seeded first and therefore always have their
    relationship neighbourhood loaded.
    """
    import re
    text = str(request or "").lower()
    seeds = []
    for eid, meta in (store.master_metadata().get("entities") or {}).items():
        for name in {str(eid), str((meta or {}).get("name") or "")}:
            name = name.strip().lower()
            if len(name) >= 2 and re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", text):
                seeds.append(("entity", str(eid), 0))
                break
    return seeds


def _semantic_context(store, request, max_chars=10000):
    """Load a bounded two-hop connected subgraph; semantic judgment remains with the LLM."""
    from ragapp.core.retrieval import RetrievalStore
    retrieval = RetrievalStore(store)
    candidates = retrieval.search_metadata_candidates(str(request or ""), limit=10).get("candidates", [])
    queue = _named_entity_seeds(store, request)
    queue += [(str(c.get("kind") or ""), str(c.get("id") or ""), 0) for c in candidates]
    seen, ordered = set(), []
    while queue and len(ordered) < 32:
        kind, ident, depth = queue.pop(0)
        if kind not in CANONICAL_KINDS or not ident or (kind, ident) in seen:
            continue
        seen.add((kind, ident)); ordered.append((kind, ident))
        if depth >= 2:
            continue
        rec = store._read_entity(ident) if kind == "entity" else store._read_object(kind, ident)
        refs = []
        for field, ref_kind in (
            ("relationships", "relationship"), ("relationship_ids", "relationship"),
            ("events", "event"), ("event_ids", "event"), ("location_ids", "location"),
            ("concept_ids", "concept"), ("definition_ids", "definition"),
            ("knowledge_ids", "knowledge"), ("related_entity_ids", "entity"),
            ("related_event_ids", "event"), ("related_concept_ids", "concept"),
        ):
            for value in rec.get(field) or []:
                rid = value.get("id") if isinstance(value, dict) else value
                if rid: refs.append((ref_kind, str(rid)))
        for participant in rec.get("participants") or []:
            if isinstance(participant, dict) and participant.get("id"):
                refs.append((str(participant.get("kind") or "entity"), str(participant["id"])))
        for ref in refs:
            if ref not in seen: queue.append((ref[0], ref[1], depth + 1))
    requests = [{"kind": kind, "id": ident, "detail": "section", "sections": ["description", "summary", "attributes", "participants", "relationships", "events", "location_ids", "concept_ids", "definition_ids", "knowledge_ids", "timeline", "evolution"]} for kind, ident in ordered]
    return retrieval.retrieve(requests, max_chars=max_chars).get("requests") or []


def _mutation_prompt(request, context, source_recompile=False):
    schema = {
        "operations": [
            {"operation": "delete_object", "kind": "entity|relationship|event|location|concept|definition|knowledge", "id": "...", "reason": "..."},
            {"operation": "state_update", "entity": "...", "field": "...", "new": None, "reason": "...", "timeline": None},
            {"operation": "replace_fields", "kind": "entity|relationship|event|location|concept|definition|knowledge", "id": "...", "fields": {}, "reason": "..."},
            {"operation": "upsert_entity", "id": "...", "data": {}, "reason": "..."},
            {"operation": "upsert_object", "kind": "relationship|event|location|concept|definition|knowledge", "id": "...", "data": {}, "reason": "..."},
        ],
        "affected": [{"kind": "...", "id": "...", "reason": "..."}],
        "explanation": "short transaction rationale",
    }
    source_rule = (
        "\n7. Supporting source will be recompiled under this authoritative change. That does NOT remove your responsibility "
        "to emit semantic consequences in connected canonical cognition. If the requested change makes a relationship, "
        "summary, state, concept, event, or knowledge claim false or incomplete, emit the minimal operation needed to "
        "reconcile it. The source remains evidence, not authority over the newer user-approved current state."
        if source_recompile else ""
    )
    instructions = (
        "You are applying an explicit user-approved mutation to persistent canonical cognition.\n"
        "The requested change is authoritative for this transaction. Existing canonical cognition is the PRE-MUTATION state to transform, never a reason to reject or argue against the requested change. "
        "Determine ALL semantic consequences inside the supplied connected cognition. If a current relationship, ordering, summary, label, or derived claim contradicts what follows from the authoritative new state plus unchanged connected facts, you MUST reconcile that claim in this transaction; do not require the user to name the consequence separately. "
        "Do not invent unrelated changes. Preserve superseded facts as history/provenance when appropriate, but never preserve them as current truth. Preserve historical facts when the request changes current state; delete historical material only when the user explicitly requests deletion/erasure of the object or fact itself.\n"
        "Return ONLY JSON matching the supplied schema.\n"
        "Rules:\n"
        "1. If an entity is explicitly deleted entirely, emit delete_object for it and semantic edits/deletions for connected cognition whose meaning becomes false or incomplete.\n"
        "2. Do not emit mechanical dangling-reference cleanup; the runtime performs that automatically after deletions.\n"
        "3. replace_fields deliberately rewrites ONLY named top-level fields. Use it when old derived prose/state is no longer true.\n"
        "4. state_update is for durable entity attribute evolution, not erasure.\n"
        "5. Keep operations minimal but complete across relationships, events, knowledge, concepts and summaries that are semantically affected.\n"
        "6. Never modify provenance merely to hide the origin of retained knowledge.\n"
        "7. Treat every current claim in the connected cognition as subject to reconciliation. Any claim whose truth, meaning, validity, or completeness depends on changed state must be updated, invalidated, or removed when the resulting state no longer supports it."
        + source_rule
    )
    return (
        instructions
        + "\n\nSCHEMA:\n" + json.dumps(schema, ensure_ascii=False)
        + "\n\nUSER-APPROVED CHANGE:\n" + str(request)
        + "\n\nCONNECTED CANONICAL COGNITION:\n" + json.dumps(context, ensure_ascii=False, default=str)
    )


def _verification_prompt(request, context):
    """Ask the semantic model to verify closure of the resulting connected state."""
    schema = {
        "valid": True,
        "conflicts": [{"kind": "...", "id": "...", "reason": "..."}],
        "operations": [
            {"operation": "delete_object", "kind": "entity|relationship|event|location|concept|definition|knowledge", "id": "...", "reason": "..."},
            {"operation": "state_update", "entity": "...", "field": "...", "new": None, "reason": "...", "timeline": None},
            {"operation": "replace_fields", "kind": "entity|relationship|event|location|concept|definition|knowledge", "id": "...", "fields": {}, "reason": "..."},
            {"operation": "upsert_entity", "id": "...", "data": {}, "reason": "..."},
            {"operation": "upsert_object", "kind": "relationship|event|location|concept|definition|knowledge", "id": "...", "data": {}, "reason": "..."},
        ],
    }
    instructions = (
        "Verify semantic closure after an authoritative cognition mutation. "
        "The requested mutation is authoritative current state. Inspect ALL supplied connected cognition, not only directly edited objects. "
        "A result is valid only when no current claim is false, contradictory, stale, or materially incomplete as a consequence of the mutation together with unchanged connected facts. "
        "Do not defend pre-mutation cognition against the mutation. Do not require the user to enumerate consequences. "
        "If invalid, return the minimal repair operations needed to make the connected current state semantically consistent. "
        "Do not invent unrelated changes. Preserve historical/provenance evidence unless it is itself claimed as current state. "
        "Set valid=true only when no repair operation is required. Return ONLY JSON matching the schema."
    )
    return (
        instructions
        + "\n\nSCHEMA:\n" + json.dumps(schema, ensure_ascii=False)
        + "\n\nAUTHORITATIVE MUTATION:\n" + str(request)
        + "\n\nRESULTING CONNECTED COGNITION:\n" + json.dumps(context, ensure_ascii=False, default=str)
    )


def _verify_and_repair_semantic_closure(store, request, source_label, pre_commit, max_context_chars, max_passes=3):
    """Bounded generic semantic invariant: verify -> repair -> reverify."""
    applied = []
    reports = []
    generator = _semantic_generator(store)
    for pass_no in range(1, max_passes + 1):
        context = _semantic_context(store, request, max_chars=max_context_chars)
        report = json.loads(generator(_verification_prompt(request, context)))
        if not isinstance(report, dict) or not isinstance(report.get("operations", []), list):
            raise ValueError("semantic verification returned an invalid result")
        reports.append({
            "pass": pass_no,
            "valid": bool(report.get("valid")),
            "conflicts": list(report.get("conflicts") or []),
            "operations_requested": len(report.get("operations") or []),
        })
        operations = list(report.get("operations") or [])
        if bool(report.get("valid")) and not operations:
            return applied, reports
        if not operations:
            raise RuntimeError("semantic verification found unresolved conflicts but produced no repair operations")
        repaired, rejected = _apply_semantic_operations(store, operations, source_label, pre_commit)
        if rejected:
            raise RuntimeError("semantic verification repair contained rejected operations: " + json.dumps(rejected, ensure_ascii=False))
        applied.extend(repaired)
    # Never commit a mutation merely because the repair budget was exhausted.
    context = _semantic_context(store, request, max_chars=max_context_chars)
    final = json.loads(generator(_verification_prompt(request, context)))
    if not isinstance(final, dict):
        raise ValueError("final semantic verification returned an invalid result")
    reports.append({
        "pass": max_passes + 1,
        "valid": bool(final.get("valid")),
        "conflicts": list(final.get("conflicts") or []),
        "operations_requested": len(final.get("operations") or []),
    })
    if not bool(final.get("valid")) or final.get("operations"):
        raise RuntimeError("semantic cognition mutation did not reach a consistent closed state: " + json.dumps(final.get("conflicts") or [], ensure_ascii=False))
    return applied, reports


def _strip_reference(value, deleted_kind, deleted_id):
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                iid = str(item.get("id") or item.get("entity_id") or item.get("object_id") or "")
                ikind = str(item.get("kind") or "")
                if iid == deleted_id and (not ikind or ikind == deleted_kind):
                    continue
                out.append(_strip_reference(item, deleted_kind, deleted_id))
            elif str(item) == deleted_id:
                continue
            else:
                out.append(item)
        return out
    if isinstance(value, dict):
        return {k: _strip_reference(v, deleted_kind, deleted_id) for k, v in value.items()}
    return value


def _delete_object(store, kind, ident):
    kind, ident = str(kind), str(ident)
    if kind not in CANONICAL_KINDS:
        raise ValueError(f"unsupported cognition kind: {kind}")
    path = store._entity_path(ident) if kind == "entity" else store._object_path(kind, ident)
    if path and path.exists(): path.unlink()
    if kind == "entity":
        master = store.master_metadata(); master.get("entities", {}).pop(ident, None); store._write_master(master)
        store.db.delete_entity(ident)
    for other_kind in CANONICAL_KINDS:
        for rec in list(store._all_kind_records(other_kind)):
            oid = str(rec.get("id") or "")
            cleaned = _strip_reference(rec, kind, ident)
            if cleaned != rec:
                cleaned["updated_at"] = now_iso()
                opath = store._entity_path(oid) if other_kind == "entity" else store._object_path(other_kind, oid)
                if opath: store._write_json(opath, cleaned)
    if store.timeline_path.exists():
        timeline = store._read_json(store.timeline_path, {}); entries = timeline.get("entries") or []
        kept = [e for e in entries if not (str(e.get("kind") or "") == kind and str(e.get("object_id") or "") == ident)]
        if kept != entries: timeline["entries"] = kept; store._write_json(store.timeline_path, timeline)


def _replace_fields(store, kind, ident, fields):
    if kind not in CANONICAL_KINDS or not isinstance(fields, dict):
        raise ValueError("replace_fields requires a canonical kind and fields object")
    path = store._entity_path(ident) if kind == "entity" else store._object_path(kind, ident)
    rec = store._read_json(path, {}) if path else {}
    if not rec: raise ValueError(f"unknown {kind}: {ident}")
    protected = {"id", "schema_version", "created_at", "provenance"}
    for key, value in fields.items():
        if key in protected: continue
        if value is None: rec.pop(key, None)
        else: rec[key] = value
    rec["updated_at"] = now_iso(); store._write_json(path, rec)
    if kind == "entity": store.db.sync_entity_index(store, ident)


def _apply_semantic_operations(store, operations, source_label, pre_commit):
    applied, rejected = [], []
    for op in operations or []:
        if not isinstance(op, dict): continue
        try:
            operation = str(op.get("operation") or "")
            if operation == "delete_object":
                _delete_object(store, str(op.get("kind") or ""), str(op.get("id") or ""))
            elif operation == "replace_fields":
                _replace_fields(store, str(op.get("kind") or ""), str(op.get("id") or ""), op.get("fields") or {})
            elif operation == "state_update":
                eid, field = str(op.get("entity") or ""), str(op.get("field") or "")
                if not eid or not field: raise ValueError("state_update requires entity and field")
                prior = store.latest_attribute_state(eid, field)
                requested = op.get("new")
                # Planner may return a structured state object. The store API already
                # creates the state envelope, so never nest that envelope inside value.
                if isinstance(requested, dict) and "value" in requested:
                    requested_value = requested.get("value")
                    requested_summary = requested.get("summary") or op.get("reason", "")
                    requested_timeline = requested.get("timeline", op.get("timeline"))
                else:
                    requested_value = requested
                    requested_summary = op.get("reason", "")
                    requested_timeline = op.get("timeline")
                store.upsert_entity_update(
                    {"id": eid, "attributes": {field: {"value": requested_value, "summary": requested_summary, "timeline": requested_timeline}}},
                    change_metadata={"origin": source_label, "change_type": "semantic_state_update", "authority": "durable", "reason": op.get("reason", ""), "pre_state_git_commit": pre_commit, "requested_value": requested_value, "previous_value": prior.get("value") if isinstance(prior, dict) else None},
                )
            elif operation == "upsert_entity":
                data = dict(op.get("data") or {}); data.setdefault("id", op.get("id"))
                store.upsert_entity_update(data, change_metadata={"origin": source_label, "authority": "durable", "reason": op.get("reason", "")})
            elif operation == "upsert_object":
                kind = str(op.get("kind") or ""); data = dict(op.get("data") or {}); data.setdefault("id", op.get("id"))
                if kind == "relationship": store.add_relationship(data)
                elif kind == "event": store.add_event(data)
                elif kind in CANONICAL_KINDS - {"entity"}: store.upsert_canonical(kind, data)
                else: raise ValueError(f"unsupported upsert kind: {kind}")
            else: raise ValueError(f"unsupported semantic mutation operation: {operation}")
            applied.append(op)
        except Exception as exc:
            rejected.append({"operation": op, "error": str(exc)})
    return applied, rejected


def _source_files_for_semantic_request(store, request):
    """Resolve source files supporting the cognition most relevant to a mutation."""
    from ragapp.core.retrieval import RetrievalStore
    retrieval = RetrievalStore(store)
    try:
        candidates = retrieval.search_metadata_candidates(str(request or ""), limit=8).get("candidates", [])
    except Exception:
        candidates = []
    seeds = [(str(c.get("kind") or ""), str(c.get("id") or "")) for c in candidates]
    queue = [(k, i, 0) for k, i in seeds if k in CANONICAL_KINDS and i]
    seen, artifact_ids = set(), set()

    def collect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"artifact_id", "source_artifact"} and isinstance(child, str) and child.startswith("source:"):
                    artifact_ids.add(child)
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    while queue and len(seen) < 24:
        kind, ident, depth = queue.pop(0)
        if (kind, ident) in seen:
            continue
        seen.add((kind, ident))
        rec = store._read_entity(ident) if kind == "entity" else store._read_object(kind, ident)
        if not isinstance(rec, dict) or not rec:
            continue
        collect(rec)
        if depth >= 2:
            continue
        # Traverse explicit canonical references; this is structural selection only.
        for field, ref_kind in (("relationships", "relationship"), ("events", "event"), ("locations", "location"),
                                ("concepts", "concept"), ("definitions", "definition"), ("knowledge_links", "knowledge"),
                                ("relationship_ids", "relationship"), ("event_ids", "event"), ("location_ids", "location"),
                                ("concept_ids", "concept"), ("definition_ids", "definition"), ("knowledge_ids", "knowledge"),
                                ("related_entity_ids", "entity"), ("related_event_ids", "event"), ("related_concept_ids", "concept")):
            for value in rec.get(field) or []:
                rid = value.get("id") if isinstance(value, dict) else value
                if rid:
                    queue.append((ref_kind, str(rid), depth + 1))
        for participant in rec.get("participants") or []:
            if isinstance(participant, dict) and participant.get("id"):
                queue.append((str(participant.get("kind") or "entity"), str(participant["id"]), depth + 1))
        for field in ("source", "target"):
            rid = rec.get(field)
            if rid:
                queue.append((str(rec.get(field + "_kind") or "entity"), str(rid), depth + 1))

    files = []
    for artifact_id in sorted(artifact_ids):
        rel = artifact_id[len("source:"):]
        path = store.source / rel
        if path.exists() and path.is_file():
            files.append(("source", path))
    return files


def _recompile_authoritative_sources(store, changes, request, files=None):
    """Recompile supporting source under explicit user-approved current-state overrides."""
    files = files if files is not None else _source_files_for_semantic_request(store, request)
    if not files:
        return None
    from ragapp.cognition.document_compiler import DocumentCognitionCompiler
    return DocumentCognitionCompiler(store).compile_files(files, authoritative_changes=changes)


def apply_semantic_mutation(store: CognitionStore, changes, source_label="agent", max_context_chars=10000, recompile_source=False):
    """Apply arbitrary approved cognition changes as one semantic transaction.

    Order: (1) Git commit of the current state, (2) load the connected subgraph,
    (3) plan minimal operations, (4) apply them, (5) verify/repair closure,
    (6) commit. Only the affected canonical objects are edited; source documents
    are NOT re-extracted unless ``recompile_source=True``.
    """
    import shutil, tempfile
    from pathlib import Path
    from ragapp.core.metadata_sync import sync_store
    request = "\n".join(f"- {str(x)}" for x in changes if str(x).strip()) if isinstance(changes, (list, tuple)) else str(changes or "").strip()
    if not request: raise ValueError("semantic cognition mutation requires requested changes")
    # (1) VCS checkpoint FIRST, before anything is loaded or planned.
    pre_commit = store.commit_authoritative_change("Pre-state backup before semantic cognition mutation")
    context = _semantic_context(store, request, max_chars=max_context_chars)
    source_files = _source_files_for_semantic_request(store, request) if recompile_source else []
    plan = json.loads(_semantic_generator(store)(_mutation_prompt(request, context, source_recompile=bool(source_files))))
    if not isinstance(plan, dict) or not isinstance(plan.get("operations"), list):
        raise ValueError("semantic cognition mutation returned an invalid operation plan")
    with tempfile.TemporaryDirectory(prefix="cognition-mutation-") as tmp:
        backup = Path(tmp) / "cognition"; shutil.copytree(store.cognition, backup)
        try:
            applied, rejected = _apply_semantic_operations(store, plan.get("operations"), source_label, pre_commit)
            if rejected:
                raise RuntimeError("semantic cognition mutation contained rejected operations: " + json.dumps(rejected, ensure_ascii=False))
            # Source-derived cognition is rebuilt under the approved override instead
            # of relying on surgical JSON edits alone. The source remains unchanged;
            # the explicit current-state mutation outranks conflicting old source facts.
            recompilation = None
            if source_files:
                recompilation = _recompile_authoritative_sources(
                    store,
                    [str(x) for x in changes] if isinstance(changes, (list, tuple)) else [request],
                    request,
                    files=source_files,
                )

            # Re-plan once against the post-recompile graph, then enforce a
            # generic semantic-closure invariant. The transaction is not allowed
            # to commit while any connected current claim remains inconsistent
            # with the authoritative mutation and unchanged connected state.
            post_plan, post_applied = {}, []
            if source_files:  # re-plan only when a recompile could have re-introduced stale facts
                post_context = _semantic_context(store, request, max_chars=max_context_chars)
                post_plan = json.loads(_semantic_generator(store)(_mutation_prompt(request, post_context, source_recompile=False)))
                if not isinstance(post_plan, dict) or not isinstance(post_plan.get("operations"), list):
                    raise ValueError("post-recompile semantic reconciliation returned an invalid operation plan")
                post_applied, post_rejected = _apply_semantic_operations(store, post_plan.get("operations"), source_label, pre_commit)
                if post_rejected:
                    raise RuntimeError("post-recompile semantic reconciliation contained rejected operations: " + json.dumps(post_rejected, ensure_ascii=False))
                applied.extend(post_applied)

            verification_applied, verification_reports = _verify_and_repair_semantic_closure(
                store, request, source_label, pre_commit, max_context_chars
            )
            applied.extend(verification_applied)

            store.mark_compiled(); sync_store(store)
            git_commit = store.commit_authoritative_change("Apply semantic cognition mutation")
            affected = list(plan.get("affected") or [])
            for item in post_plan.get("affected") or []:
                if item not in affected:
                    affected.append(item)
            return {"status":"applied", "requested_changes":request, "affected":affected, "applied":applied, "explanation":post_plan.get("explanation") or plan.get("explanation", ""), "pre_state_git_commit":pre_commit, "git_commit":git_commit, "recompilation":recompilation, "post_reconciliation": {"performed": True, "operations": len(post_applied)}, "semantic_verification": {"performed": True, "repairs_applied": len(verification_applied), "passes": verification_reports}}
        except Exception:
            if store.cognition.exists(): shutil.rmtree(store.cognition)
            shutil.copytree(backup, store.cognition); sync_store(store); raise


def merge_deltas(store: CognitionStore, deltas, source_label="generation", events=None):
    applied = []
    rejected = []

    # A durable mutation must have a recoverable pre-mutation revision. Git is
    # the backup/version layer; canonical cognition remains the runtime source
    # of truth. Capture the state immediately before the first permanent
    # mutation in this batch.
    permanent_deltas = [d for d in (deltas or []) if isinstance(d, dict) and d.get("permanence", "transient") == "permanent"]
    pre_commit = None
    if permanent_deltas:
        pre_commit = store.commit_authoritative_change("Pre-state backup before durable cognition update")

    for delta in deltas or []:
        try:
            permanence = delta.get("permanence", "transient")
            if permanence not in ALLOWED_PERMANENCE:
                raise ValueError("permanence must be permanent or transient")

            operation = delta.get("operation")
            if permanence == "transient":
                store.append_event({"type": "transient_delta", "source": source_label, "delta": delta})
                applied.append({**delta, "applied": False, "reason": "transient"})
                continue

            if operation == "create_entity":
                eid = str(delta.get("entity") or "")
                if not eid:
                    raise ValueError("entity is required")
                payload = dict(delta.get("entity_data") or {})
                payload.setdefault("id", eid)
                payload.setdefault("name", eid)
                payload.setdefault("type", "other")
                store.upsert_entity_update(payload, source_artifact=delta.get("source"), default_timeline=delta.get("timeline"))
                applied.append({**delta, "applied": True})
                continue

            if operation == "create_relationship":
                rel = dict(delta.get("relationship") or {})
                if not rel.get("id"):
                    raise ValueError("relationship.id is required")
                store.add_relationship(rel, source_artifact=delta.get("source"), timeline=delta.get("timeline"))
                applied.append({**delta, "applied": True})
                continue

            if operation == "create_event":
                event = dict(delta.get("event") or {})
                if not event.get("id"):
                    raise ValueError("event.id is required")
                store.add_event(event, source_artifact=delta.get("source"), timeline=delta.get("timeline"), sequence=delta.get("sequence"))
                applied.append({**delta, "applied": True})
                continue

            if operation == "create_knowledge":
                record = dict(delta.get("knowledge") or {})
                if not record.get("id"):
                    raise ValueError("knowledge.id is required")
                store.upsert_canonical("knowledge", record, source_artifact=delta.get("source"), timeline=delta.get("timeline"), location=delta.get("locator"))
                applied.append({**delta, "applied": True})
                continue

            # Default durable operation: append a new state observation rather
            # than replacing the previous state.
            eid = str(delta.get("entity") or "")
            field = str(delta.get("field") or "")
            if not eid or not field:
                raise ValueError("entity and field are required")
            if eid not in store.master_metadata().get("entities", {}):
                raise ValueError(f"unknown entity: {eid}")
            prior = store.latest_attribute_state(eid, field)
            store.upsert_entity_update(
                {
                    "id": eid,
                    "attributes": {
                        field: {
                            "value": delta.get("new"),
                            "summary": delta.get("reason", ""),
                            "timeline": delta.get("timeline"),
                            "valid_from": delta.get("valid_from"),
                            "valid_to": delta.get("valid_to"),
                            "event_id": delta.get("event_id"),
                            "locator": delta.get("locator"),
                        }
                    },
                },
                source_artifact=delta.get("source"),
                default_timeline=delta.get("timeline"),
                change_metadata={
                    "origin": source_label,
                    "change_type": "state_update",
                    "authority": "durable",
                    "reason": delta.get("reason", ""),
                    "pre_state_git_commit": pre_commit,
                    "requested_value": delta.get("new"),
                    "previous_value": prior.get("value") if isinstance(prior, dict) else None,
                },
            )
            applied.append({**delta, "applied": True})
        except Exception as exc:
            rejected.append({"delta": delta, "error": str(exc)})

    for event in events or []:
        try:
            if isinstance(event, dict) and event.get("id"):
                store.add_event(event, source_artifact=event.get("source_artifact"), timeline=event.get("timeline"), sequence=event.get("sequence"))
        except Exception as exc:
            rejected.append({"event": event, "error": str(exc)})

    store.mark_compiled()
    git_commit = None
    if applied:
        git_commit = store.commit_authoritative_change("Merge durable cognition")
    return {
        "version": store.master_metadata().get("current_version", 0),
        "applied": applied,
        "rejected": rejected,
        "pre_state_git_commit": pre_commit,
        "git_commit": git_commit,
    }