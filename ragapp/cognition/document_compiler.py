"""Structure-aware compiler for non-code documents.

The compiler deliberately separates:
  * structural parsing (deterministic, no LLM),
  * semantic extraction (LLM), and
  * source-location indexing (deterministic).

Only compact locators are persisted in the cognition index. Source text is
read again only when a later retrieval request actually needs it.
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
from ragapp.cognition.store import now_iso

SCHEMA = r'''
You are compiling one structured document segment into durable cognition.
Return ONLY valid JSON.

{
  "project_type": "domain|fiction|research|business|other",
  "instructions": "optional durable rules, concise",
  "entities": {
    "stable_id": {
      "type": "character|organization|location|artifact|concept|object|rule|other",
      "name": "",
      "summary": "",
      "tags": [],
      "attributes": {},
      "evidence_node_ids": []
    }
  },
  "events": [
    {
      "id": "stable id when possible",
      "type": "",
      "title": "",
      "description": "",
      "entities": [],
      "sequence": null,
      "narrative_position": {"label": "", "sequence": null, "story_time": ""},
      "evidence_node_ids": []
    }
  ],
  "relationships": {
    "stable_relationship_id": {
      "type": "",
      "source": "entity id",
      "target": "entity id",
      "state": "",
      "description": "",
      "evidence_node_ids": []
    }
  },
  "state_changes": [
    {
      "entity_id": "",
      "attribute": "",
      "previous_value": null,
      "new_value": null,
      "description": "",
      "evidence_node_ids": []
    }
  ]
}

Rules:
1. Never invent facts or locations.
2. Reuse KNOWN entity/event IDs when the same thing is clearly present.
3. evidence_node_ids MUST contain only IDs present in SOURCE NODES.
4. The compiler will attach the exact physical locator to every returned item.
5. Distinguish document order from narrative/story time.
6. For fiction, capture character appearances, events, relationship changes,
   creations/destructions, arrivals/departures, discoveries, decisions and
   other durable state transitions.
7. A relationship change should be represented as a state_change and/or event,
   not merely overwrite a previous relationship state.
8. Do not reproduce source passages in the JSON.
9. Chapter/scene/section identity comes from STRUCTURE CONTEXT; do not invent
   page numbers, paragraph numbers, slide numbers or cell coordinates.
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


def _known_entities(store):
    return {
        eid: {"type": e.get("type"), "name": e.get("name"), "summary": e.get("summary", "")}
        for eid, e in store.master_metadata().get("entities", {}).items()
    }


def _known_events(store):
    return [
        {"id": e.get("id"), "type": e.get("type"), "title": e.get("title"), "description": e.get("description", ""), "entities": e.get("entities", [])}
        for e in store.events(limit=500)
    ]


def _valid_evidence(ids, allowed):
    return [x for x in (ids or []) if isinstance(x, str) and x in allowed]


def _locations_for(segment, parsed, evidence_ids):
    ids = _valid_evidence(evidence_ids, set(segment.node_ids)) or list(segment.node_ids)
    nodes = [parsed.nodes[x] for x in ids if x in parsed.nodes]
    loc = dict(segment.locator)
    if nodes:
        first, last = nodes[0], nodes[-1]
        loc = dict(first.locator or {})
        for k, v in (last.locator or {}).items():
            if k in {"page", "paragraph_index", "slide", "row"}:
                loc[f"{k}_end"] = v
            elif k.endswith("_end"):
                loc[k] = v
        if "paragraph_index" in loc and "paragraph_end" not in loc:
            loc["paragraph_start"] = loc.get("paragraph_index")
            loc["paragraph_end"] = loc.get("paragraph_index_end", loc.get("paragraph_index"))
        if "page" in loc and "page_start" not in loc:
            loc["page_start"] = loc.get("page")
            loc["page_end"] = loc.get("page_end", loc.get("page"))
        if "slide" in loc and "slide_start" not in loc:
            loc["slide_start"] = loc.get("slide")
            loc["slide_end"] = loc.get("slide_end", loc.get("slide"))
        if "row" in loc and "row_start" not in loc:
            loc["row_start"] = loc.get("row")
            loc["row_end"] = loc.get("row_end", loc.get("row"))
        loc["node_ids"] = ids
    return loc


def _entity_update(item, segment, parsed, artifact_id):
    evidence = _valid_evidence(item.get("evidence_node_ids"), set(segment.node_ids))
    location = {
        "artifact_id": artifact_id,
        "segment_id": segment.id,
        "node_ids": evidence or list(segment.node_ids),
        "locator": _locations_for(segment, parsed, evidence),
        "section_ids": list(segment.section_ids),
        "chapter_id": segment.chapter_id,
        "scene_id": segment.scene_id,
    }
    update = {
        "id": item.get("id") or item.get("stable_id") or item.get("name"),
        "type": item.get("type", "other"),
        "name": item.get("name") or item.get("id") or item.get("stable_id"),
        "summary": item.get("summary", ""),
        "tags": item.get("tags", []),
        "attributes": item.get("attributes", {}),
        "locations": [location],
    }
    return update, location


def _event_record(item, segment, parsed, artifact_id):
    evidence = _valid_evidence(item.get("evidence_node_ids"), set(segment.node_ids))
    location = {
        "artifact_id": artifact_id,
        "segment_id": segment.id,
        "node_ids": evidence or list(segment.node_ids),
        "locator": _locations_for(segment, parsed, evidence),
        "section_ids": list(segment.section_ids),
        "chapter_id": segment.chapter_id,
        "scene_id": segment.scene_id,
    }
    rec = dict(item)
    rec["document_locations"] = [location]
    rec["source_locator"] = location["locator"]
    rec["source_artifacts"] = [artifact_id]
    return rec, location


def _relationship_record(item, segment, parsed, artifact_id):
    evidence = _valid_evidence(item.get("evidence_node_ids"), set(segment.node_ids))
    location = {
        "artifact_id": artifact_id,
        "segment_id": segment.id,
        "node_ids": evidence or list(segment.node_ids),
        "locator": _locations_for(segment, parsed, evidence),
        "section_ids": list(segment.section_ids),
        "chapter_id": segment.chapter_id,
        "scene_id": segment.scene_id,
    }
    rec = dict(item)
    rec["document_locations"] = [location]
    return rec, location


class DocumentCognitionCompiler:
    def __init__(self, store):
        self.store = store
        self.index = DocumentIndex(store)

    def compile_files(self, files: Iterable[tuple[str, Path]], progress_callback=None) -> dict:
        files = list(files)
        generator, provider, model = _provider_generator(self.store)
        parsed_docs=[]; failures=[]
        per_segment_budget=max(5000, int(MAX_CONTEXT_CHARS * 0.48))
        for area, path in files:
            rel=path.relative_to(self.store.source if area == "source" else self.store.workspace).as_posix()
            try:
                self.store.register_artifact(area, rel, artifact_type=path.suffix.lower().lstrip(".") or "file")
                parsed=parse_document(path, area=area, max_chars=per_segment_budget)
                # Parser works from a real filesystem path; cognition provenance
                # must instead use the project-relative artifact identity.
                parsed.path=rel
                parsed.artifact_id=f"{area}:{rel}"
                for node in parsed.nodes.values():
                    node.locator=dict(node.locator or {})
                self.index.replace_document(parsed)
                parsed_docs.append(parsed)
            except Exception as exc:
                failures.append({"area":area,"file":rel,"error":str(exc)})
        if any(f.get("area") == "source" for f in failures):
            raise RuntimeError("Document parsing failed: " + json.dumps(failures, ensure_ascii=False))
        segments=[]
        for doc in parsed_docs:
            for seg in doc.segments:
                segments.append((doc,seg))
        skipped=[]; processed=0
        for i,(doc,seg) in enumerate(segments,1):
            allowed=set(seg.node_ids)
            structure={
                "artifact_id":doc.artifact_id,
                "document":doc.title,
                "format":doc.format,
                "segment_id":seg.id,
                "chapter_id":seg.chapter_id,
                "scene_id":seg.scene_id,
                "section_ids":seg.section_ids,
                "locator":seg.locator,
                "nodes":[
                    {"id":n.id,"type":n.type,"title":n.title,"locator":n.locator,"attributes":{k:v for k,v in n.attributes.items() if k not in {"runs"}}}
                    for nid in seg.node_ids if (n:=doc.nodes.get(nid)) is not None
                ],
            }
            prompt=(SCHEMA+"\n\nKNOWN ENTITIES:\n"+json.dumps(_known_entities(self.store),ensure_ascii=False)
                    +"\n\nKNOWN EVENTS:\n"+json.dumps(_known_events(self.store),ensure_ascii=False)
                    +"\n\nSTRUCTURE CONTEXT:\n"+json.dumps(structure,ensure_ascii=False)
                    +"\n\nSOURCE NODES (text is supplied only for this compilation call):\n"+seg.text)
            try:
                raw=generator(prompt)
                data=json.loads(raw)
            except Exception as exc:
                try:
                    data=json.loads(generator(prompt+"\n\nReturn valid JSON only; repair the previous response."))
                except Exception:
                    skipped.append({"segment":seg.id,"error":str(exc)})
                    if progress_callback: progress_callback(i,len(segments))
                    continue
            if not isinstance(data,dict):
                skipped.append({"segment":seg.id,"error":"non_object_json"})
                if progress_callback: progress_callback(i,len(segments))
                continue
            artifact_id=doc.artifact_id
            for sid,item in (data.get("entities") or {}).items():
                if not isinstance(item,dict): continue
                item=dict(item); item.setdefault("id",sid)
                update, location=_entity_update(item,seg,doc,artifact_id)
                self.store.upsert_entity_update(update,source_artifact=artifact_id,chunk_index=i,default_timeline=seg.title,default_sequence=i)
                self.index.attach("entity",update["id"],artifact_id,node_ids=location["node_ids"],segment_id=seg.id,locator=location["locator"],section_ids=location["section_ids"],chapter_id=seg.chapter_id,scene_id=seg.scene_id)
            for item in data.get("events") or []:
                if not isinstance(item,dict): continue
                rec, location=_event_record(item,seg,doc,artifact_id)
                rec.setdefault("narrative_position",{})
                if isinstance(rec["narrative_position"],dict):
                    rec["narrative_position"].setdefault("sequence", rec.get("sequence") or i)
                    rec["narrative_position"].setdefault("label", seg.title)
                event_id = self.store.add_event(rec,source_artifact=artifact_id,timeline=seg.title,sequence=rec.get("sequence") or i,narrative_position=rec.get("narrative_position"))
                if event_id:
                    self.index.attach("event",event_id,artifact_id,node_ids=location["node_ids"],segment_id=seg.id,locator=location["locator"],section_ids=location["section_ids"],chapter_id=seg.chapter_id,scene_id=seg.scene_id)
            for rid,item in (data.get("relationships") or {}).items():
                if not isinstance(item,dict): continue
                item=dict(item); item.setdefault("id",rid)
                rec,location=_relationship_record(item,seg,doc,artifact_id)
                self.store.add_relationship(rec,source_artifact=artifact_id,timeline=seg.title)
                self.index.attach("relationship",rec["id"],artifact_id,node_ids=location["node_ids"],segment_id=seg.id,locator=location["locator"],section_ids=location["section_ids"],chapter_id=seg.chapter_id,scene_id=seg.scene_id)
            # State changes become explicit temporal attributes on the affected entity.
            for change in data.get("state_changes") or []:
                if not isinstance(change,dict) or not change.get("entity_id") or not change.get("attribute"): continue
                eid=str(change["entity_id"])
                existing_meta=self.store.master_metadata().get("entities",{}).get(eid,{})
                update={"id":eid,"type":existing_meta.get("type","other"),"name":existing_meta.get("name",eid),"summary":change.get("description","") or existing_meta.get("summary","") or "","attributes":{
                    str(change["attribute"]): {"value":change.get("new_value"),"summary":change.get("description","") or "","timeline":seg.title,"previous_value":change.get("previous_value")}
                },"locations":[{"artifact_id":artifact_id,"segment_id":seg.id,"node_ids":_valid_evidence(change.get("evidence_node_ids"),allowed) or list(seg.node_ids),"locator":_locations_for(seg,doc,change.get("evidence_node_ids")),"section_ids":list(seg.section_ids),"chapter_id":seg.chapter_id,"scene_id":seg.scene_id}]}
                # Only apply if the entity already exists; state changes should not create hallucinated entities.
                if eid in self.store.master_metadata().get("entities",{}):
                    self.store.upsert_entity_update(update,source_artifact=artifact_id,chunk_index=i,default_timeline=seg.title,default_sequence=i)
                    self.index.attach("entity",eid,artifact_id,node_ids=update["locations"][0]["node_ids"],segment_id=seg.id,locator=update["locations"][0]["locator"],section_ids=seg.section_ids,chapter_id=seg.chapter_id,scene_id=seg.scene_id)
                    change_event={
                        "id": f"event:state_change:{eid}:{change.get('attribute')}:{i}",
                        "type": "state_change",
                        "title": change.get("description") or f"{eid} {change.get('attribute')} changed",
                        "description": change.get("description") or f"{eid} {change.get('attribute')} changed from {change.get('previous_value')!r} to {change.get('new_value')!r}",
                        "entities": [eid],
                        "sequence": i,
                        "narrative_position": {"label": seg.title, "sequence": i},
                        "document_locations": update["locations"],
                        "source_locator": update["locations"][0]["locator"],
                        "source_artifacts": [artifact_id],
                    }
                    change_event_id=self.store.add_event(change_event,source_artifact=artifact_id,timeline=seg.title,sequence=i,narrative_position=change_event["narrative_position"])
                    if change_event_id:
                        self.index.attach("event",change_event_id,artifact_id,node_ids=update["locations"][0]["node_ids"],segment_id=seg.id,locator=update["locations"][0]["locator"],section_ids=seg.section_ids,chapter_id=seg.chapter_id,scene_id=seg.scene_id)
            processed += 1
            if data.get("project_type"): self.store.set_project_type(data["project_type"])
            if data.get("instructions"):
                existing=self.store.instructions_path.read_text(encoding="utf-8") if self.store.instructions_path.exists() else ""
                lines={x.strip() for x in existing.splitlines() if x.strip()}
                new_lines=[x.strip() for x in str(data.get("instructions")).splitlines() if x.strip() and x.strip() not in lines]
                self.store.instructions_path.write_text((existing + ("\n" if existing and new_lines else "") + "\n".join(new_lines)).strip(),encoding="utf-8")
            if progress_callback: progress_callback(i,len(segments))
        self.store.mark_compiled()
        sync_store(self.store)
        return {
            "status":"compiled",
            "provider":provider,
            "model":model,
            "source_files":sum(1 for a,_ in files if a=="source"),
            "workspace_files":sum(1 for a,_ in files if a=="workspace"),
            "documents":len(parsed_docs),
            "segments":len(segments),
            "processed_segments":processed,
            "failed_files":failures,
            "skipped_segments":skipped,
            "entities":len(self.store.master_metadata().get("entities",{})),
            "relationships":len(self.store.relationships()),
        }
