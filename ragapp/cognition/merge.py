"""Deterministic application of agent-generated state deltas to entity files."""
from __future__ import annotations
from ragapp.cognition.store import CognitionStore
ALLOWED_PERMANENCE={"permanent","transient"}

def _flag_related(store,eid,affected_entities,source_label,delta):
    # Mirrors ReingestPipeline's affected_files: this only flags related
    # entities as needing review after a state change — it never edits or
    # rewrites their content. If their state must also change, that needs
    # its own explicit delta.
    known=store.master_metadata().get("entities",{})
    for related_id in affected_entities or []:
        if related_id==eid or related_id not in known: continue
        store.append_event({"type":"related_entity_flagged","source":source_label,
                             "entity":related_id,"caused_by":eid,
                             "reason":delta.get("reason","")})

def merge_deltas(store:CognitionStore,deltas,source_label="generation"):
    applied=[]; rejected=[]
    for d in deltas or []:
        try:
            permanence=d.get("permanence","transient")
            if permanence not in ALLOWED_PERMANENCE: raise ValueError("permanence must be permanent or transient")
            if d.get("operation")=="create_entity":
                if permanence!="permanent":raise ValueError("create_entity must be permanent")
                eid=d.get("entity")
                if not eid:raise ValueError("entity is required")
                if eid in store.master_metadata().get("entities",{}):raise ValueError(f"entity already exists: {eid}")
                data=dict(d.get("entity_data") or {}); data.setdefault("id",eid); data.setdefault("type","other"); data.setdefault("name",eid)
                store.upsert_entity_update(data,source_artifact=d.get("source"),default_timeline=d.get("timeline"))
                if d.get("initial_state"):
                    store.upsert_entity_update({"id":eid,"attributes":d["initial_state"]},source_artifact=d.get("source"),default_timeline=d.get("timeline"))
                store.append_event({"type":"entity_created","source":source_label,"entity":eid,"data":data})
                applied.append({**d,"applied":True}); continue
            eid=d.get("entity"); field=d.get("field")
            if not eid or not field:raise ValueError("entity and field are required")
            if eid not in store.master_metadata().get("entities",{}):raise ValueError(f"unknown entity: {eid}")
            if permanence=="transient":
                store.append_event({"type":"transient_delta","source":source_label,"delta":d}); applied.append({**d,"applied":False,"reason":"transient"}); continue
            store.upsert_entity_update({"id":eid,"attributes":{field:{"value":d.get("new"),"summary":d.get("reason",""),"timeline":d.get("timeline"),"valid_from":d.get("valid_from"),"valid_to":d.get("valid_to"),"locator":d.get("locator")}}},source_artifact=d.get("source"),default_timeline=d.get("timeline"))
            store.append_event({"type":"state_delta","source":source_label,"delta":d})
            _flag_related(store,eid,d.get("affected_entities"),source_label,d)
            applied.append({**d,"applied":True})
        except Exception as exc:rejected.append({"delta":d,"error":str(exc)})
    store.mark_compiled()
    return {"version":store.master_metadata().get("current_version",0),"applied":applied,"rejected":rejected}