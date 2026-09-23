"""Entity-centric persistent cognition storage.
The filesystem is authoritative for entity knowledge. SQLite is a compact local index.
Entity updates are append-oriented: temporal states are added rather than overwriting history.
"""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from ragapp import settings
from ragapp.core.metadata import MetadataDB

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value).strip())
    return value.strip("._") or "item"

def _timeline_rank(value):
    if value is None:return None
    m=re.search(r"(?:chapter|ch\.|version|v|year|scene|part)\s*([0-9]+)",str(value),re.I)
    if m:return int(m.group(1))
    m=re.search(r"\b([0-9]+)\b",str(value))
    return int(m.group(1)) if m else None

class CognitionStore:
    def __init__(self, username: str, project_id: str):
        self.username=slug(username.lower()); self.project_id=slug(project_id)
        self.root = Path(settings.PROJECTS_ROOT) / slug(username.lower()) / slug(project_id)
        self.root = self.root.resolve()
        self.cognition=self.root/"cognition"; self.entities_root=self.cognition/"entities"; self.index_root=self.cognition/"_index"; self.events_root=self.cognition/"events"
        self.timeline=self.cognition/"timeline"; self.source=self.root/"source"; self.workspace=self.root/"workspace"; self.sessions=self.root/"sessions"; self.log=self.root/"log"
        for p in (self.cognition,self.entities_root,self.index_root,self.timeline,self.events_root,self.source,self.workspace,self.sessions,self.log): p.mkdir(parents=True,exist_ok=True)
        self.master_metadata_path=self.index_root/"master_metadata.json"
        self.state_map_path=self.cognition/"state_map.json"  # compatibility view only
        self.ledger_path=self.cognition/"ledger.json"        # compatibility view only
        self.relationships_path=self.cognition/"relationships.json"  # compatibility view only
        self.events_path=self.cognition/"events.jsonl"
        self.instructions_path=self.cognition/"system_instructions.md"
        self.summaries_path=self.cognition/"summaries.json"
        self.relationship_history_path=self.cognition/"relationship_history.json"
        self._db=None
        self._document_index=None
        
    @property
    def document_index(self):
        if self._document_index is None:
            from ragapp.cognition.document_index import DocumentIndex
            self._document_index = DocumentIndex(self)
        return self._document_index

    @property
    def db(self):
        if self._db is None:self._db=MetadataDB(self)
        return self._db
    
    def exists(self): return self.master_metadata_path.exists()
    
    def ensure_initialized(self):
        if self.exists():return False
        self._write_json(self.master_metadata_path,{"schema_version":2,"project_id":self.project_id,"project_type":"domain","current_version":0,"entities":{},"artifacts":{},"categories":{},"timeline":[]})
        self._write_json(self.state_map_path,{"project_id":self.project_id,"type":"domain","entities":{},"timeline":[],"current_version":0,"compiled":False})
        self._write_json(self.ledger_path,{})
        self._write_json(self.relationships_path,{})
        self._write_json(self.summaries_path,{})
        self._write_json(self.relationship_history_path,{})
        self.instructions_path.write_text("",encoding="utf-8")
        self.events_path.touch(exist_ok=True); self.events_root.mkdir(parents=True,exist_ok=True)
        self.document_index
        return 
    def reset_source_derived_cognition(self):
        """
        Remove cognition that is derived from the authoritative source
        before a complete source rebuild.

        This intentionally does NOT remove:
          - workspace/
          - source/
          - log/
          - sessions/
          - git history
          - project configuration

        It resets the entity/relationship/index projection so that a
        subsequent compilation represents the current source tree rather
        than accumulating stale entities from previous source versions.
        """
        import shutil

        # Remove entity JSON projection.
        if self.entities_root.exists():
            for child in self.entities_root.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                elif child.is_file():
                    child.unlink()

        # Reset master metadata.
        self._write_json(
            self.master_metadata_path,
            {
                "schema_version": 2,
                "project_id": self.project_id,
                "project_type": "domain",
                "current_version": 0,
                "entities": {},
                "artifacts": {},
                "categories": {},
                "timeline": [],
            },
        )

        # Reset the SQLite metadata projection.
        db = self.db
        with db.conn() as conn:
            conn.executescript(
                """
                DELETE FROM entity_tags;
                DELETE FROM sections;
                DELETE FROM attribute_states;
                DELETE FROM relations;
                DELETE FROM events;
                DELETE FROM source_chunks;
                DELETE FROM entities;
                DELETE FROM artifacts;
                """
            )
        # The structural index is a derived projection and is rebuilt by the
        # next compilation pass. Do not retain stale page/chapter locators.
        self._document_index = None
        self.document_index._write({"schema_version": 1, "documents": {}, "nodes": {}, "segments": {}, "entity_locations": {}, "event_locations": {}, "relationship_locations": {}})
        self._write_json(self.relationship_history_path,{})
            
    def _read_json(self,path,default):
        if not path.exists():return default
        try:return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError,OSError):return default
    def _write_json(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp"); tmp.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding="utf-8"); tmp.replace(path)
    def master_metadata(self): self.ensure_initialized(); return self._read_json(self.master_metadata_path,{})
    def state_map(self):
        """Compact compatibility index. It deliberately contains summaries/pointers, not full entity state."""
        m=self.master_metadata(); entities={}
        for eid,e in m.get("entities",{}).items(): entities[eid]={"type":e.get("type","other"),"tags":e.get("tags",[]),"summary":e.get("summary",""),"path":e.get("path"),"sections":e.get("sections",[])}
        return {"project_id":self.project_id,"type":m.get("project_type","domain"),"entities":entities,"timeline":m.get("timeline",[]),"current_version":m.get("current_version",0),"compiled":bool(entities)}
    def ledger(self):
        """Compatibility view. Prefer targeted retrieval APIs for agent use."""
        out={}
        for eid in self.master_metadata().get("entities",{}):
            entity=self._read_entity(eid)
            current={}
            for attr,states in entity.get("attributes",{}).items():
                if states: current[attr]=states[-1].get("value")
            out[eid]=current
        return out
    def relationships(self):
        return {r["id"]:r for r in self.db.relations()}
    def summaries(self): return {eid:e.get("summary","") for eid,e in self.master_metadata().get("entities",{}).items()}
    def _entity_path(self,entity_id,entity_type="other"):
        return self.entities_root/slug(entity_type)/f"{slug(entity_id)}.json"
    def _read_entity(self,entity_id):
        meta=self.master_metadata().get("entities",{}).get(entity_id)
        if not meta:return {}
        return self._read_json(self.root/meta["path"],{})
    def _write_master(self,m): self._write_json(self.master_metadata_path,m)
    
    def register_artifact(self,area,path,summary="", description="", artifact_type=None,):
        if area not in ("source", "workspace"):
            raise ValueError(f"Invalid artifact area: {area!r}")

        root = (
            self.source
            if area == "source"
            else self.workspace
        ).resolve()

        p = (root / path).resolve()

        try:
            rel = p.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(
                f"Artifact path escapes {area} directory: {path!r}"
            ) from exc

        if not p.is_file():
            raise FileNotFoundError(
                f"Artifact does not exist or is not a file: {p}"
            )

        stat = p.stat()
        h = hashlib.sha256(p.read_bytes()).hexdigest()

        aid = f"{area}:{rel}"

        rec = {
            "id": aid,
            "area": area,
            "path": rel,
            "name": p.name,
            "type": artifact_type
            or p.suffix.lower().lstrip(".")
            or "file",
            "summary": summary,
            "description": description,
            "sha256": h,
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(
                stat.st_mtime,
                timezone.utc,
            ).isoformat(),
        }

        self.db.upsert_artifact(rec)

        m = self.master_metadata()
        m.setdefault("artifacts", {})[aid] = rec
        self._write_master(m)

        return rec
    
    def _ensure_entity(self, eid, etype, name, summary, tags, path=None):
        m = self.master_metadata()
        existing = m.setdefault("entities", {}).get(eid)
        entity_path = (
            Path(existing["path"])
            if existing and existing.get("path")
            else Path(
                path
                or self._entity_path(eid, etype)
                .relative_to(self.root)
                .as_posix()
            )
        )
        full = self.root / entity_path
        data = self._read_json(full, {}) if full.exists() else {
            "schema_version": 2,
            "id": eid,
            "type": etype,
            "name": name,
            "summary": summary,
            "tags": sorted(set(tags or [])),
            "sections": {},
            "attributes": {},
            "relationships": [],
            "events": [],
            "sources": [],
            "source_projections": {},
            "locations": [],
            "created_at": now_iso(),
        }
        data["type"] = etype or data.get("type", "other")
        data["name"] = name or data.get("name", eid)
        if summary:
            data["summary"] = summary
        else:
            data.setdefault("summary", "")
        data["tags"] = sorted(
            set(data.get("tags", [])) | {str(x) for x in (tags or [])}
        )
        data.setdefault("sections", {})
        data.setdefault("attributes", {})
        data.setdefault("relationships", [])
        data.setdefault("events", [])
        data.setdefault("sources", [])
        data.setdefault("source_projections", {})
        data.setdefault("locations", [])
        data["updated_at"] = now_iso()
        self._write_json(full, data)
        m["entities"][eid] = {
            "id": eid,
            "type": data["type"],
            "name": data["name"],
            "summary": data.get("summary", ""),
            "tags": data.get("tags", []),
            "path": entity_path.as_posix(),
            "sections": sorted(data.get("sections", {})),
            "sources": sorted(data.get("sources", [])),
        }
        self._write_master(m)
        self.db.upsert_entity({**m["entities"][eid], "description": data.get("summary", "")})
        return data

    @staticmethod
    def _append_attribute_to_data(data, attr, value, timeline, source_artifact,
                                  locator=None, summary="", valid_from=None,
                                  valid_to=None, sequence_hint=None):
        states = data.setdefault("attributes", {}).setdefault(attr, [])
        vf = valid_from or timeline
        signature = json.dumps(
            {
                "value": value,
                "timeline": timeline,
                "source": source_artifact,
                "locator": locator,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        for state in states:
            existing = json.dumps(
                {
                    "value": state.get("value"),
                    "timeline": state.get("timeline"),
                    "source": state.get("source_artifact"),
                    "locator": state.get("source_locator"),
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            if existing == signature:
                return
        seq = len(states) + 1
        states.append({
            "value": value,
            "summary": summary,
            "timeline": timeline,
            "timeline_sequence": sequence_hint if sequence_hint is not None else _timeline_rank(timeline),
            "valid_from": vf,
            "valid_to": valid_to,
            "sequence": seq,
            "source_artifact": source_artifact,
            "source_locator": locator,
            "recorded_at": now_iso(),
        })

    def _apply_update_to_data(self, data, update, source_artifact=None,
                              chunk_index=0, default_timeline=None,
                              default_sequence=None):
        timeline = update.get("timeline") or default_timeline
        source_meta = {"artifact": source_artifact, "chunk": chunk_index}

        for location in update.get("locations") or []:
            if not isinstance(location, dict):
                continue
            normalized = {**location}
            normalized.setdefault("artifact_id", source_artifact)
            existing = data.setdefault("locations", [])
            signature = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
            if not any(json.dumps(x, sort_keys=True, ensure_ascii=False) == signature for x in existing):
                existing.append(normalized)

        sections = update.get("sections") or {}
        if isinstance(sections, list):
            sections = {
                str(x.get("name") or x.get("id")): x
                for x in sections
                if isinstance(x, dict) and (x.get("name") or x.get("id"))
            }

        for key, value in sections.items():
            if not isinstance(value, dict):
                section = data.setdefault("sections", {}).setdefault(
                    key, {"summary": "", "content": [], "tags": []}
                )
                section.setdefault("content", []).append({
                    "value": value,
                    "timeline": timeline,
                    "source": source_meta,
                })
                continue

            section = data.setdefault("sections", {}).setdefault(
                key, {"summary": "", "content": [], "tags": []}
            )
            if value.get("summary"):
                section["summary"] = value["summary"]
            section["tags"] = sorted(
                set(section.get("tags", []))
                | {str(x) for x in (value.get("tags") or [])}
            )
            content = value.get("content")
            if content:
                section.setdefault("content", []).append({
                    "value": content,
                    "timeline": value.get("timeline", timeline),
                    "source": source_meta,
                })
            for key2, value2 in (value.get("attributes") or {}).items():
                self._append_attribute_to_data(
                    data,
                    key2,
                    value2,
                    value.get("timeline", timeline),
                    source_artifact,
                    value.get("locator"),
                    value.get("summary", ""),
                )

        attrs = update.get("attributes") or {}
        if isinstance(attrs, dict):
            for attr, value in attrs.items():
                if isinstance(value, dict) and any(
                    k in value for k in ("value", "state", "valid_from", "valid_to", "timeline")
                ):
                    self._append_attribute_to_data(
                        data,
                        attr,
                        value.get("value", value.get("state")),
                        value.get("timeline", timeline),
                        source_artifact,
                        value.get("locator"),
                        value.get("summary", ""),
                        value.get("valid_from") or value.get("timeline", timeline),
                        value.get("valid_to"),
                        value.get("sequence", default_sequence),
                    )
                else:
                    self._append_attribute_to_data(
                        data,
                        attr,
                        value,
                        timeline,
                        source_artifact,
                        None,
                        "",
                        None,
                        None,
                        default_sequence,
                    )

        for rel in update.get("relationships") or []:
            if isinstance(rel, dict):
                data.setdefault("relationships", []).append({
                    **rel,
                    "source_artifact": source_artifact,
                    "timeline": rel.get("timeline", timeline),
                })

        # Event records are first-class cognition objects stored under
        # cognition/events/*.json. Entity JSON stores only references to those
        # canonical event objects; it never embeds a second copy of an event.
        for event in update.get("events") or []:
            if isinstance(event, dict) and (event.get("id") or event.get("event_id")):
                event_id = str(event.get("id") or event.get("event_id"))
                refs = data.setdefault("events", [])
                if not any(isinstance(ref, dict) and ref.get("id") == event_id for ref in refs):
                    refs.append({"id": event_id})

    def _rebuild_entity_from_projections(self, eid, data):
        projections = data.get("source_projections") or {}
        if not projections:
            return data

        first = None
        for aid in sorted(projections):
            records = projections.get(aid) or []
            if records:
                first = records[0].get("update") or {}
                break

        if first is None:
            return data

        rebuilt = {
            "schema_version": 2,
            "id": eid,
            "type": first.get("type", data.get("type", "other")),
            "name": first.get("name", data.get("name", eid)),
            "summary": "",
            "tags": [],
            "sections": {},
            "attributes": {},
            "relationships": [],
            "events": [],
            "sources": sorted(projections),
            "source_projections": projections,
            "locations": [],
            "created_at": data.get("created_at", now_iso()),
        }

        ordered = []
        for aid, records in projections.items():
            for record in records or []:
                ordered.append((
                    int(record.get("chunk_index", 0)),
                    record.get("recorded_at", ""),
                    aid,
                    record.get("update") or {},
                ))
        ordered.sort(key=lambda x: (x[0], x[1], x[2]))

        for chunk_index, _, aid, update in ordered:
            rebuilt["type"] = update.get("type") or rebuilt["type"]
            rebuilt["name"] = update.get("name") or rebuilt["name"]
            if update.get("summary"):
                rebuilt["summary"] = update["summary"]
            rebuilt["tags"] = sorted(
                set(rebuilt["tags"]) | {str(x) for x in (update.get("tags") or [])}
            )
            self._apply_update_to_data(
                rebuilt,
                update,
                source_artifact=aid,
                chunk_index=chunk_index,
                default_timeline=update.get("timeline") or f"chunk {chunk_index}",
            )

        rebuilt["updated_at"] = now_iso()
        return rebuilt

    def upsert_entity_update(self, update, source_artifact=None, chunk_index=0,
                             default_timeline=None, default_sequence=None):
        eid = str(update.get("id") or update.get("stable_id") or update.get("name"))
        if not eid:
            raise ValueError("entity update requires id")

        etype = update.get("type", "other")
        name = update.get("name") or eid
        summary = update.get("summary", "")
        tags = update.get("tags", [])

        data = self._ensure_entity(
            eid, etype, name, summary, tags
        )

        if source_artifact:
            projections = data.setdefault("source_projections", {})
            projections.setdefault(source_artifact, []).append({
                "chunk_index": chunk_index,
                "recorded_at": now_iso(),
                "update": json.loads(json.dumps(update, ensure_ascii=False)),
            })
            data = self._rebuild_entity_from_projections(eid, data)
        else:
            self._apply_update_to_data(
                data,
                update,
                source_artifact=None,
                chunk_index=chunk_index,
                default_timeline=default_timeline,
                default_sequence=default_sequence,
            )

        path = self.master_metadata()["entities"][eid]["path"]
        self._write_json(self.root / path, data)
        self._ensure_entity(
            eid,
            data.get("type", etype),
            data.get("name", name),
            data.get("summary", summary),
            data.get("tags", tags),
        )

        # Keep SQLite's derived attribute index synchronized with the
        # authoritative entity JSON. Durable STATE_UPDATE calls previously
        # updated the JSON correctly but left attribute_states stale, so
        # subsequent retrieval could return the old source-derived value.
        self.db.sync_entity_index(self, eid)
        return eid

    def remove_source_projection(self, source_artifacts):
        """Remove only source-derived cognition for the supplied artifacts."""
        source_artifacts = set(source_artifacts or [])
        if not source_artifacts:
            return

        master = self.master_metadata()
        for aid in source_artifacts:
            try:
                self.document_index.remove_artifact(aid)
            except Exception:
                pass
        entities_to_delete = []

        for eid, meta in list(master.get("entities", {}).items()):
            path = meta.get("path")
            if not path:
                continue

            entity = self._read_json(self.root / path, {})
            projections = entity.get("source_projections") or {}

            if projections:
                changed = False
                for aid in list(projections):
                    if aid in source_artifacts:
                        projections.pop(aid, None)
                        changed = True

                if changed:
                    entity["source_projections"] = projections
                    if projections:
                        entity = self._rebuild_entity_from_projections(eid, entity)
                    else:
                        # No source projections remain. Keep non-source
                        # cognition only if it exists; otherwise remove the
                        # source-derived entity entirely.
                        entities_to_delete.append(eid)
                        continue
                    self._write_json(self.root / path, entity)
                    meta.update({
                        "type": entity.get("type", meta.get("type", "other")),
                        "name": entity.get("name", meta.get("name", eid)),
                        "summary": entity.get("summary", ""),
                        "tags": entity.get("tags", []),
                        "sections": sorted(entity.get("sections", {})),
                        "sources": sorted(projections),
                    })
                continue

            # Legacy entities created before source_projections existed.
            # Filter all explicitly provenance-tagged contributions. If the
            # entity had no remaining source provenance, remove it.
            sources = set(entity.get("sources", []))
            if not sources.intersection(source_artifacts):
                continue

            remaining = sources - source_artifacts
            entity["sources"] = sorted(remaining)

            for attr, states in list((entity.get("attributes") or {}).items()):
                entity["attributes"][attr] = [
                    s for s in states
                    if s.get("source_artifact") not in source_artifacts
                ]
                if not entity["attributes"][attr]:
                    entity["attributes"].pop(attr, None)

            for section_key, section in list((entity.get("sections") or {}).items()):
                if isinstance(section, dict):
                    section["content"] = [
                        c for c in section.get("content", [])
                        if (c.get("source") or {}).get("artifact") not in source_artifacts
                    ]
                    if not section.get("content") and not section.get("summary"):
                        entity["sections"].pop(section_key, None)

            entity["relationships"] = [
                r for r in entity.get("relationships", [])
                if r.get("source_artifact") not in source_artifacts
            ]
            entity["events"] = [
                e for e in entity.get("events", [])
                if e.get("source_artifact") not in source_artifacts
            ]
            entity["locations"] = [
                loc for loc in entity.get("locations", [])
                if loc.get("artifact_id") not in source_artifacts
            ]

            if not remaining:
                entities_to_delete.append(eid)
            else:
                self._write_json(self.root / path, entity)
                meta.update({
                    "type": entity.get("type", meta.get("type", "other")),
                    "name": entity.get("name", meta.get("name", eid)),
                    "summary": entity.get("summary", ""),
                    "tags": entity.get("tags", []),
                    "sections": sorted(entity.get("sections", {})),
                    "sources": sorted(remaining),
                })

        if entities_to_delete:
            self.db.delete_relations_for_entities(entities_to_delete)

        for eid in entities_to_delete:
            meta = master.get("entities", {}).pop(eid, None)
            if meta and meta.get("path"):
                full = self.root / meta["path"]
                if full.exists():
                    full.unlink()

        for aid in source_artifacts:
            master.get("artifacts", {}).pop(aid, None)

        self._write_master(master)

        # Reconcile canonical event provenance with the replaced source artifacts.
        # Events supported by another source survive; otherwise the event object
        # itself is removed.
        for event_path in list(self.events_root.glob("*.json")):
            event = self._read_json(event_path, {})
            event_sources = list(dict.fromkeys(
                [str(x) for x in (event.get("source_artifacts") or []) if x]
                + ([str(event["source_artifact"])] if event.get("source_artifact") else [])
            ))
            remaining_sources = [x for x in event_sources if x not in source_artifacts]
            if len(remaining_sources) != len(event_sources):
                if remaining_sources:
                    event["source_artifacts"] = remaining_sources
                    event["source_artifact"] = remaining_sources[0]
                    event["updated_at"] = now_iso()
                    self._write_json(event_path, event)
                else:
                    event_path.unlink(missing_ok=True)

        # Remove source-scoped relational/index records, then rebuild the
        # entity/section/attribute projection from the remaining JSON.
        # Remove dangling event references from entity JSONs after canonical
        # event records for replaced artifacts have been removed.
        remaining_event_ids = {str(e.get("id")) for e in self.events(limit=100000) if e.get("id")}
        for eid, meta in list(self.master_metadata().get("entities", {}).items()):
            path = meta.get("path")
            if not path:
                continue
            entity_path = self.root / path
            entity = self._read_json(entity_path, {})
            refs = entity.get("events") or []
            filtered = [ref for ref in refs if isinstance(ref, dict) and str(ref.get("id")) in remaining_event_ids]
            if len(filtered) != len(refs):
                entity["events"] = filtered
                entity["updated_at"] = now_iso()
                self._write_json(entity_path, entity)

        history=self._read_json(self.relationship_history_path,{})
        for rid, entries in list(history.items()):
            kept=[e for e in entries if e.get("source_artifact") not in source_artifacts]
            if kept: history[rid]=kept
            else: history.pop(rid,None)
        self._write_json(self.relationship_history_path,history)

        for aid in source_artifacts:
            self.db.delete_relations_for_artifact(aid)

        # Rebuild the current relation projection from surviving relationship
        # history. A relationship may be supported by multiple documents;
        # deleting one source must not accidentally delete the relation if a
        # surviving source still supports it.
        for rid, entries in history.items():
            if not entries:
                continue
            latest=entries[-1]
            self.db.add_relation({
                "id":rid, "from_id":latest.get("source"), "to_id":latest.get("target"),
                "relation_type":latest.get("type","related_to"),
                "description":latest.get("description",latest.get("state","")),
                "timeline":latest.get("timeline"),
                "source_artifact":latest.get("source_artifact"),
            })
            self.db.delete_events_for_artifact(aid)
            self.db.delete_source_chunks_for_artifact(aid)
            self.db.delete_artifact(aid)

        # Relation rows were just removed from SQLite. Refresh entity JSON
        # projections so deleted source relationships cannot remain stale.
        self._sync_relationships_for_entities(
            self.master_metadata().get("entities", {}).keys()
        )

        self.db.rebuild_entity_index(self)
        self.db.rebuild_event_index(self)

    def _append_attribute(self,data,eid,attr,value,timeline,source_artifact,locator,summary="",valid_from=None,valid_to=None,sequence_hint=None):
        states=data.setdefault("attributes",{}).setdefault(attr,[]); vf=valid_from or timeline
        # Deduplicate the exact same update; genuine changes are appended.
        signature=json.dumps({"value":value,"timeline":timeline,"source":source_artifact,"locator":locator},sort_keys=True,ensure_ascii=False)
        if any(json.dumps({"value":s.get("value"),"timeline":s.get("timeline"),"source":s.get("source_artifact"),"locator":s.get("source_locator")},sort_keys=True,ensure_ascii=False)==signature for s in states): return
        seq=len(states)+1; rec={"value":value,"summary":summary,"timeline":timeline,"timeline_sequence":sequence_hint if sequence_hint is not None else _timeline_rank(timeline),"valid_from":vf,"valid_to":valid_to,"sequence":seq,"source_artifact":source_artifact,"source_locator":locator,"recorded_at":now_iso()}; states.append(rec)
        self.db.add_attribute_state({"id":f"{eid}:{attr}:{seq}","entity_id":eid,"attribute":attr,"value":value,"summary":summary,"valid_from":vf,"valid_to":valid_to,"sequence":seq,"source_artifact":source_artifact,"source_locator":locator})
    def _sync_entity_relationships(self, entity_id):
        """Project the authoritative SQLite relationships into an entity JSON.

        SQLite remains the graph index, but entity files must also expose the
        relationships so an entity is self-describing when loaded directly.
        Missing/non-entity targets are simply ignored.
        """
        meta = self.master_metadata().get("entities", {}).get(entity_id)
        if not meta or not meta.get("path"):
            return

        path = self.root / meta["path"]
        entity = self._read_json(path, {})
        if not entity:
            return

        relations = [
            dict(r)
            for r in self.db.relations_for([entity_id])
        ]
        entity["relationships"] = relations
        entity["updated_at"] = now_iso()
        self._write_json(path, entity)

    def _sync_relationships_for_entities(self, entity_ids):
        for entity_id in set(str(x) for x in (entity_ids or []) if x):
            self._sync_entity_relationships(entity_id)

    def add_relationship(self,rel,default_source=None,source_artifact=None,timeline=None,data=None):
        if not isinstance(rel,dict):return
        frm=str(rel.get("source") or rel.get("from") or default_source or ""); to=str(rel.get("target") or rel.get("to") or "")
        if not frm or not to:return
        rid=str(rel.get("id") or f"{frm}::{rel.get('type','related_to')}::{to}")
        rec={"id":rid,"source":frm,"target":to,"type":rel.get("type",rel.get("relation_type","related_to")),"description":rel.get("description",rel.get("state","")),"state":rel.get("state"),"timeline":rel.get("timeline",timeline),"source_artifact":source_artifact,"document_locations":list(rel.get("document_locations") or []),"recorded_at":now_iso()}
        history=self._read_json(self.relationship_history_path,{})
        entries=history.setdefault(rid,[])
        signature=json.dumps({k:rec.get(k) for k in ("source","target","type","state","description","timeline","source_artifact","document_locations")},sort_keys=True,ensure_ascii=False)
        if not any(json.dumps({k:x.get(k) for k in ("source","target","type","state","description","timeline","source_artifact","document_locations")},sort_keys=True,ensure_ascii=False)==signature for x in entries):
            entries.append(rec)
            self._write_json(self.relationship_history_path,history)
        self.db.add_relation({"id":rid,"from_id":frm,"to_id":to,"relation_type":rec["type"],"description":rec["description"],"timeline":rec["timeline"],"source_artifact":source_artifact})
        if data is not None and not any(x.get("id")==rid and x.get("timeline")==rec["timeline"] for x in data.setdefault("relationships",[])): data["relationships"].append(rec)

        # Keep the filesystem entity projection in sync with the authoritative
        # relationship index. Both ends get the edge so loading either entity
        # directly exposes its graph context.
        self._sync_relationships_for_entities((frm, to))
        for loc in rel.get("document_locations") or []:
            try:
                self.document_index.attach("relationship", rid, loc.get("artifact_id") or source_artifact,
                                          node_ids=loc.get("node_ids"), segment_id=loc.get("segment_id"),
                                          locator=loc.get("locator"), section_ids=loc.get("section_ids"),
                                          chapter_id=loc.get("chapter_id"), scene_id=loc.get("scene_id"))
            except Exception:
                pass
    def _event_path(self, event_id):
        return self.events_root / f"{slug(event_id)}.json"

    @staticmethod
    def _event_key(event):
        payload = {
            "type": event.get("type") or event.get("event_type") or "event",
            "title": event.get("title") or "",
            "description": event.get("description") or event.get("event") or "",
            "entities": sorted(str(x) for x in (event.get("entities") or [])),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:20]

    def add_event(
        self,
        event,
        source_artifact=None,
        timeline=None,
        default_entity=None,
        data=None,
        sequence=None,
        narrative_position=None,
    ):
        """Persist one narrative event as a first-class cognition object.

        The canonical record lives in ``cognition/events/<event_id>.json``.
        Entity files contain only event references. SQLite is a derived index.
        """
        if not isinstance(event, dict):
            return None

        entities = [
            str(x)
            for x in (event.get("entities") or ([default_entity] if default_entity else []))
            if x
        ]
        description = str(event.get("description") or event.get("event") or "").strip()
        if not description:
            return None

        event_id = str(
            event.get("id")
            or event.get("event_id")
            or f"event_{self._event_key(event)}"
        )
        path = self._event_path(event_id)
        existing = self._read_json(path, {}) if path.exists() else {}

        pos = event.get("narrative_position")
        if not isinstance(pos, dict):
            pos = {}
        if narrative_position:
            pos = {**narrative_position, **pos}
        if timeline and not pos.get("label"):
            pos["label"] = timeline
        if sequence is not None and pos.get("sequence") is None:
            pos["sequence"] = sequence

        source_artifacts = list(dict.fromkeys(
            [str(x) for x in (existing.get("source_artifacts") or []) if x]
            + ([str(existing["source_artifact"])] if existing.get("source_artifact") else [])
            + ([str(source_artifact)] if source_artifact else [])
            + [str(x) for x in (event.get("source_artifacts") or []) if x]
            + ([str(event["source_artifact"])] if event.get("source_artifact") else [])
        ))

        rec = {**existing, **event}
        rec.update({
            "schema_version": 2,
            "id": event_id,
            "type": event.get("type") or event.get("event_type") or existing.get("type", "event"),
            "title": event.get("title") or existing.get("title") or description[:120],
            "description": description,
            "entities": list(dict.fromkeys(entities)),
            "source_artifacts": source_artifacts,
            "location": event.get("location", existing.get("location")),
            "narrative_position": pos,
            "sequence": event.get("sequence", existing.get("sequence", sequence)),
            "previous_events": list(dict.fromkeys(str(x) for x in (event.get("previous_events") or existing.get("previous_events") or []))),
            "next_events": list(dict.fromkeys(str(x) for x in (event.get("next_events") or existing.get("next_events") or []))),
            "provenance": event.get("provenance", existing.get("provenance", "source_derived")),
            "source_artifact": existing.get("source_artifact") or source_artifact or event.get("source_artifact") or (source_artifacts[0] if source_artifacts else None),
            "source_locator": event.get("source_locator") or event.get("locator") or existing.get("source_locator"),
            "document_locations": list(event.get("document_locations") or existing.get("document_locations") or []),
            "created_at": existing.get("created_at", now_iso()),
            "updated_at": now_iso(),
        })
        self._write_json(path, rec)
        self.db.add_event({
            "id": event_id,
            "description": rec["description"],
            "entities": rec["entities"],
            "timeline": (rec.get("narrative_position") or {}).get("label") or timeline,
            "source_artifact": rec.get("source_artifact"),
            "source_locator": (json.dumps(rec.get("source_locator"), ensure_ascii=False) if isinstance(rec.get("source_locator"), (dict, list)) else rec.get("source_locator")),
            "created_at": rec["created_at"],
        })

        for entity_id in rec["entities"]:
            self.link_event_to_entity(entity_id, event_id)

        for loc in rec.get("document_locations") or []:
            try:
                self.document_index.attach("event", event_id, loc.get("artifact_id") or source_artifact,
                                          node_ids=loc.get("node_ids"), segment_id=loc.get("segment_id"),
                                          locator=loc.get("locator"), section_ids=loc.get("section_ids"),
                                          chapter_id=loc.get("chapter_id"), scene_id=loc.get("scene_id"))
            except Exception:
                pass

        if data is not None:
            refs = data.setdefault("events", [])
            if not any(isinstance(ref, dict) and ref.get("id") == event_id for ref in refs):
                refs.append({"id": event_id})
        return event_id

    def link_event_to_entity(self, entity_id, event_id):
        """Attach a canonical event reference to an entity without duplicating the event."""
        meta = self.master_metadata().get("entities", {}).get(str(entity_id))
        if not meta or not meta.get("path"):
            return
        path = self.root / meta["path"]
        entity = self._read_json(path, {})
        if not entity:
            return
        refs = entity.setdefault("events", [])
        event_id = str(event_id)
        if not any(isinstance(ref, dict) and ref.get("id") == event_id for ref in refs):
            refs.append({"id": event_id})
            entity["updated_at"] = now_iso()
            self._write_json(path, entity)

    def events(self, limit=1000):
        records = []
        if self.events_root.exists():
            for path in self.events_root.glob("*.json"):
                rec = self._read_json(path, {})
                if rec:
                    records.append(rec)

        def key(rec):
            pos = rec.get("narrative_position") or {}
            seq = rec.get("sequence", pos.get("sequence"))
            try:
                seq_key = int(seq)
            except (TypeError, ValueError):
                seq_key = 10**12
            return (seq_key, str(rec.get("created_at", "")), str(rec.get("id", "")))

        records.sort(key=key)
        return records[:limit]

    def event(self, event_id):
        return self._read_json(self._event_path(str(event_id)), {})

    def append_event(self,event):
        with self.events_path.open("a",encoding="utf-8") as f:f.write(json.dumps({"timestamp":now_iso(),**event},ensure_ascii=False)+"\n")
    def record_compilation_chunk(self,artifact,chunk_index,content=None,summary="",locator=None):
        """Record compilation provenance without persisting source body text."""
        aid=artifact.get("id") if isinstance(artifact,dict) else artifact
        self.db.add_source_chunk({"artifact_id":aid,"area":artifact.get("area") if isinstance(artifact,dict) else None,"path":artifact.get("path") if isinstance(artifact,dict) else str(artifact),"chunk_index":chunk_index,"locator":locator,"summary":summary,"content":""})
    def set_project_type(self,value):
        m=self.master_metadata(); m["project_type"]=value or m.get("project_type","domain"); self._write_master(m)
    def mark_compiled(self):
        m=self.master_metadata(); m["current_version"]=int(m.get("current_version",0))+1; m.setdefault("timeline",[]).append({"version":m["current_version"],"timestamp":now_iso(),"label":"compile"}); self._write_master(m)
    def initialize(self,*args,**kwargs):
        """Legacy compatibility. New compilation should use entity updates directly."""
        self.ensure_initialized(); self.mark_compiled(); return self.master_metadata().get("current_version",0)
    def snapshot(self,label="update"): self.mark_compiled(); return self.master_metadata().get("current_version",0)
    def entity(self,name): return self._read_entity(name)
    def load_entities(self,names):
        out={"entities":{},"relationships":{},"missing":[]}; ids=[]
        for name in names or []:
            if name in self.master_metadata().get("entities",{}): out["entities"][name]=self._read_entity(name); ids.append(name)
            else:out["missing"].append(name)
        for r in self.db.relations_for(ids):out["relationships"][r["id"]]=r
        return out
    def matching_entities(self,text):
        tokens=set(re.findall(r"[a-zA-Z0-9_'-]+",str(text).lower())); return [x["id"] for x in self.db.search_entities(tokens,limit=50)]
    def retrieval_index(self):
        m=self.master_metadata()
        docs=self.document_index.retrieval_index(limit=50)
        return {"project_id":self.project_id,"project_type":m.get("project_type"),"current_version":m.get("current_version",0),"entities":list(m.get("entities",{}).values()),"artifacts":list(m.get("artifacts",{}).values()),"categories":m.get("categories",{}),"document_index":docs}
    def retrieve(self,requests,max_chars=12000):
        """Resolve many retrieval requests locally and return only the requested detail.
        Timeline resolution is deterministic: latest means the newest state; an as_of/chapter
        request selects the latest state at or before that point when a numeric timeline exists.
        """
        result={"requests":[]}; used=0; master=self.master_metadata()
        def choose_states(states,timeline):
            if not states:return []
            if not timeline or str(timeline).lower()=="latest":
                by={}
                for st in states:
                    attr=st.get("attribute")
                    if attr not in by or int(st.get("sequence") or 0)>int(by[attr].get("sequence") or 0): by[attr]=st
                return list(by.values())
            target=_timeline_rank(timeline); out={}
            for st in states:
                rank=st.get("timeline_sequence")
                if rank is None:rank=_timeline_rank(st.get("timeline") or st.get("valid_from"))
                if target is not None and rank is not None:
                    if rank<=target and (st.get("attribute") not in out or (_timeline_rank(out[st.get("attribute")].get("valid_from")) or -1)<rank):out[st.get("attribute")]=st
                elif str(st.get("timeline") or st.get("valid_from"))==str(timeline):out[st.get("attribute")]=st
            return list(out.values())
        for req in requests or []:
            if used>=max_chars:break
            ids=[]
            if req.get("entity_id"):ids=[str(req["entity_id"])]
            elif req.get("entity_ids"):ids=[str(x) for x in req["entity_ids"]]
            elif req.get("name"):ids=self.matching_entities(req["name"])
            elif req.get("query"):ids=self.matching_entities(req["query"])
            ids=list(dict.fromkeys(x for x in ids if x in master.get("entities",{})))
            timeline=req.get("timeline") or req.get("as_of"); detail=req.get("detail","summary"); attrs=req.get("attributes") or []
            sections_wanted=req.get("sections") or []
            for eid in ids[:20]:
                meta=master["entities"][eid]; item={"id":eid,"type":meta.get("type"),"name":meta.get("name"),"summary":meta.get("summary"),"tags":meta.get("tags",[]),"path":meta.get("path"),"sources":meta.get("sources",[])}
                entity=self._read_entity(eid)
                source_ids=entity.get("sources",[])
                if source_ids:
                    item["source_artifacts"]=[master.get("artifacts",{}).get(a) for a in source_ids if master.get("artifacts",{}).get(a)]
                if detail in {"state","section","full"}:
                    item["source_locations"] = list(entity.get("locations") or [])
                    # Entity JSON is the authoritative cognition state.
                    # SQLite is a derived index and may lag briefly after a
                    # durable update, so state retrieval must never prefer
                    # the derived index over the entity record itself.
                    all_states=[]
                    for attr_name, attr_states in (entity.get("attributes") or {}).items():
                        if attrs and attr_name not in attrs:
                            continue
                        for state in attr_states or []:
                            if not isinstance(state, dict):
                                continue
                            all_states.append({
                                "entity_id": eid,
                                "attribute": attr_name,
                                "value": state.get("value"),
                                "summary": state.get("summary", ""),
                                "valid_from": state.get("valid_from") or state.get("timeline"),
                                "valid_to": state.get("valid_to"),
                                "sequence": state.get("sequence", 0),
                                "timeline": state.get("timeline"),
                                "timeline_sequence": state.get("timeline_sequence"),
                                "source_artifact": state.get("source_artifact"),
                                "source_locator": state.get("source_locator"),
                                "created_at": state.get("recorded_at"),
                            })
                    chosen=choose_states(all_states,timeline)
                    item["attributes"]={}
                    for st in chosen:
                        item["attributes"].setdefault(st["attribute"],st)
                if detail in {"section","full"}:
                    sec=entity.get("sections",{}); wanted=sections_wanted or list(sec); item["sections"]={}
                    for key in wanted:
                        if key not in sec:continue
                        val=sec[key]
                        if isinstance(val,dict) and val.get("content"):
                            content=val.get("content",[])
                            if timeline and str(timeline).lower()!="latest":
                                target=_timeline_rank(timeline); filtered=[]
                                for c in content:
                                    rank=_timeline_rank(c.get("timeline"))
                                    if target is None or rank is None or rank<=target:filtered.append(c)
                                content=filtered[-1:] if filtered else []
                            else:content=content[-1:]
                            val={**val,"content":content}
                        item["sections"][key]=val
                if detail=="full":
                    item["relationships"]=entity.get("relationships",[])
                    event_refs=entity.get("events",[]) or []
                    item["events"]=[
                        self.event(ref.get("id"))
                        for ref in event_refs
                        if isinstance(ref,dict) and ref.get("id") and self.event(ref.get("id"))
                    ]
                    # Source bodies are intentionally never returned by the
                    # generic cognition context tool. Use read_source_location
                    # only after an exact locator has been selected.
                payload=json.dumps(item,ensure_ascii=False)
                if used+len(payload)>max_chars:break
                result["requests"].append(item); used+=len(payload)
        result["relationships"]=self.db.relations_for([x["id"] for x in result["requests"]],limit=100)
        result["used_chars"]=used; result["truncated"]=used>=max_chars
        return result

    def search_cognition_metadata(self, query, limit=8):
        """Search only persisted cognition/document metadata; never reads source bodies."""
        from ragapp.core.retrieval import RetrievalStore
        return {
            "query": query,
            "candidates": RetrievalStore(self).search_metadata(query, limit=max(1, min(int(limit or 8), 20))),
            "source_loaded": False,
        }

    def relationship_history(self, relationship_id=None):
        history=self._read_json(self.relationship_history_path,{})
        if relationship_id is None:
            return history
        return history.get(str(relationship_id),[])

    def document_structure(self, artifact_id=None, query=None, limit=200):
        idx = self.document_index
        if artifact_id:
            doc = idx.document(artifact_id)
            return {"document": doc, "sections": idx.sections(artifact_id=artifact_id, query=query, limit=limit)}
        return idx.retrieval_index(limit=limit)

    def resolve_document_section(self, query, artifact_id=None):
        section = self.document_index.resolve_section(query, artifact_id=artifact_id)
        if not section:
            return {"found": False, "query": query}
        return {"found": True, "section": section}

    def cognition_locations(self, kind, ident):
        return self.document_index.locations(kind, ident)

    def read_source_location(self, artifact_id, locator):
        """Read only the requested source range using the format-specific locator."""
        master = self.master_metadata()
        artifact = master.get("artifacts", {}).get(str(artifact_id))
        if not artifact:
            return {"error": "unknown_artifact", "artifact_id": artifact_id}
        root = self.source if artifact.get("area") == "source" else self.workspace
        path = (root / artifact.get("path", "")).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            return {"error": "invalid_artifact_path"}
        if not path.is_file():
            return {"error": "artifact_not_found", "path": artifact.get("path")}
        loc = locator or {}
        ext = path.suffix.lower()
        if ext == ".pdf":
            from pypdf import PdfReader
            r = PdfReader(str(path))
            start = max(1, int(loc.get("page_start") or loc.get("page") or 1))
            end = min(len(r.pages), int(loc.get("page_end") or loc.get("page") or start))
            line_start = loc.get("line_start")
            line_end = loc.get("line_end")
            pages = []
            for page_no in range(start, end + 1):
                raw = r.pages[page_no - 1].extract_text() or ""
                if line_start is not None or line_end is not None:
                    lines = [x.strip() for x in raw.splitlines() if x.strip()]
                    lo = int(line_start) if page_no == start and line_start is not None else 0
                    hi = int(line_end) if page_no == end and line_end is not None else len(lines) - 1
                    if hi >= lo:
                        raw = "\n".join(lines[max(0, lo):min(len(lines), hi + 1)])
                    else:
                        raw = ""
                pages.append({"page": page_no, "text": raw})
            return {"artifact_id": artifact_id, "path": artifact.get("path"), "locator": loc, "pages": pages}
        if ext == ".docx":
            from docx import Document
            d=Document(str(path)); start=int(loc.get("paragraph_start") if loc.get("paragraph_start") is not None else loc.get("paragraph_index",0)); end=int(loc.get("paragraph_end") if loc.get("paragraph_end") is not None else start)
            end=min(len(d.paragraphs)-1,end); return {"artifact_id":artifact_id,"path":artifact.get("path"),"locator":loc,"paragraphs":[{"index":i,"text":d.paragraphs[i].text,"style":d.paragraphs[i].style.name if d.paragraphs[i].style else None} for i in range(max(0,start),max(0,end)+1)]}
        if ext == ".pptx":
            from pptx import Presentation
            prs=Presentation(str(path)); start=int(loc.get("slide_start") or loc.get("slide") or 1); end=min(len(prs.slides),int(loc.get("slide_end") or loc.get("slide") or start)); slides=[]
            for i in range(start,end+1): slides.append({"slide":i,"shapes":[{"shape_index":j,"text":s.text if getattr(s,"has_text_frame",False) else ""} for j,s in enumerate(prs.slides[i-1].shapes) if getattr(s,"has_text_frame",False) and s.text]})
            return {"artifact_id":artifact_id,"path":artifact.get("path"),"locator":loc,"slides":slides}
        if ext == ".xlsx":
            from openpyxl import load_workbook
            wb=load_workbook(path,read_only=True,data_only=False); ws=wb[loc.get("sheet")] if loc.get("sheet") in wb.sheetnames else wb[wb.sheetnames[0]]; start=int(loc.get("row_start") or loc.get("row") or 1); end=int(loc.get("row_end") or loc.get("row") or start); rows=[]
            for row in ws.iter_rows(min_row=start,max_row=end): rows.append([c.value for c in row])
            return {"artifact_id":artifact_id,"path":artifact.get("path"),"locator":loc,"sheet":ws.title,"rows":rows}
        text=path.read_text(encoding="utf-8",errors="replace")
        start=int(loc.get("char_start",0)); end=int(loc.get("char_end",min(len(text),start+12000))); return {"artifact_id":artifact_id,"path":artifact.get("path"),"locator":loc,"text":text[start:end]}

    def briefing(self,task):
        """Only send the compact index to the agent. Detailed cognition is fetched through tools."""
        self.ensure_initialized(); matches=self.matching_entities(task); idx=self.retrieval_index()
        doc_index=self.document_structure(query=task,limit=20)
        return {"project":{"project_id":self.project_id,"project_type":idx["project_type"],"current_version":idx["current_version"]},"retrieval_instruction":"Use document structure/section tools first for document questions. Resolve a chapter/section to metadata-only locators, then use get_section_cognition or get_cognition_locations, and only then read_source_location for exact source text. The index contains summaries and pointers only.","matching_entities":[self.master_metadata()["entities"][x] for x in matches[:20]],"available_entities":[{"id":e["id"],"type":e.get("type"),"name":e.get("name"),"summary":e.get("summary",""),"tags":e.get("tags",[]),"path":e.get("path")} for e in idx["entities"]],"document_index":doc_index}
