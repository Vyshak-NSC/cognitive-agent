"""Durable agent-state merge into the canonical cognition model."""
from __future__ import annotations

from ragapp.cognition.store import CognitionStore

ALLOWED_PERMANENCE = {"permanent", "transient"}


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
