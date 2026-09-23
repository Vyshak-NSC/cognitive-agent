"""Canonical persistent cognition store.

This module owns the durable cognition model.  Each cognition kind has one
canonical file representation; cross-links are references only.  Source
provenance is retained on every derived object, while source bodies remain in
source/ and are loaded only on demand.
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value).strip())
    return value.strip("._") or "item"


def _timeline_rank(value: Any):
    if value is None:
        return None
    text = str(value)
    m = re.search(r"(?:chapter|ch\.|version|v|year|scene|part)\s*([0-9]+)", text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\b([0-9]+)\b", text)
    return int(m.group(1)) if m else None


class CognitionStore:
    """Filesystem-authoritative canonical cognition with SQLite as an index only."""

    SCHEMA_VERSION = 4
    CANONICAL_KINDS = ("entities", "relationships", "events", "locations", "concepts", "definitions", "knowledge")

    def __init__(self, username: str, project_id: str):
        self.username = slug(username.lower())
        self.project_id = slug(project_id)
        self.root = (Path(settings.PROJECTS_ROOT) / self.username / self.project_id).resolve()
        self.cognition = self.root / "cognition"
        self.index_root = self.cognition / "_index"
        self.entities_root = self.cognition / "entities"
        self.relationships_root = self.cognition / "relationships"
        self.events_root = self.cognition / "events"
        self.locations_root = self.cognition / "locations"
        self.concepts_root = self.cognition / "concepts"
        self.definitions_root = self.cognition / "definitions"
        self.knowledge_root = self.cognition / "knowledge"
        self.timeline_root = self.cognition / "timeline"
        self.source = self.root / "source"
        self.workspace = self.root / "workspace"
        self.sessions = self.root / "sessions"
        self.log = self.root / "log"
        self.system = self.root / ".system"

        # Do not eagerly create empty cognition-kind directories. A directory
        # appears only when that kind is actually persisted.
        for p in (self.cognition, self.index_root, self.source, self.workspace, self.sessions, self.log, self.system):
            p.mkdir(parents=True, exist_ok=True)

        self.master_metadata_path = self.index_root / "master_metadata.json"
        self._db = None
        self._document_index = None

    @property
    def document_index(self):
        if self._document_index is None:
            from ragapp.cognition.document_index import DocumentIndex
            self._document_index = DocumentIndex(self)
        return self._document_index

    @property
    def db(self):
        if self._db is None:
            self._db = MetadataDB(self)
        return self._db

    def exists(self):
        return self.master_metadata_path.exists()

    def _read_json(self, path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _write_json(self, path: Path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _write_master(self, value):
        self._write_json(self.master_metadata_path, value)

    def ensure_initialized(self):
        if self.exists():
            current = self._read_json(self.master_metadata_path, {})
            if int(current.get("schema_version", 0) or 0) < self.SCHEMA_VERSION:
                # v8.4 canonical cognition is incompatible with the previous
                # scattered projections. Force a clean source-derived rebuild.
                self._migrate_legacy_cognition()
                return True
            return False
        self._write_master({
            "schema_version": self.SCHEMA_VERSION,
            "project_id": self.project_id,
            "project_type": "domain",
            "current_version": 0,
            "artifacts": {},
            "entities": {},
            "counts": {},
            "timelines": [],
            "compiled": False,
        })
        return True

    def _migrate_legacy_cognition(self):
        import shutil
        self._remove_legacy_cognition_files()
        for root in (self.entities_root, self.relationships_root, self.events_root, self.locations_root, self.concepts_root, self.definitions_root, self.knowledge_root, self.timeline_root):
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)
        self._write_master({
            "schema_version": self.SCHEMA_VERSION,
            "project_id": self.project_id,
            "project_type": "domain",
            "current_version": 0,
            "artifacts": {},
            "entities": {},
            "counts": {},
            "timelines": [],
            "compiled": False,
        })
        if self._db is not None:
            self._db_reset()
        self._document_index = None

    def _remove_legacy_cognition_files(self):
        """Delete the old duplicate/compatibility cognition stores."""
        import shutil
        legacy_files = (
            self.cognition / "state_map.json",
            self.cognition / "ledger.json",
            self.cognition / "relationships.json",
            self.cognition / "events.jsonl",
            self.cognition / "summaries.json",
            self.cognition / "relationship_history.json",
            self.cognition / "world_model.json",
        )
        for path in legacy_files:
            path.unlink(missing_ok=True)
        # Old timeline/document projections are rebuilt by the new compiler.
        # Do not delete the canonical source tree.
        for folder in (self.cognition / "map", self.cognition / "state", self.cognition / "artifacts", self.cognition / "documents"):
            if folder.exists() and folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)

    def reset_source_derived_cognition(self):
        """Clear all source-derived cognition and rebuild from current source."""
        import shutil

        self.ensure_initialized()
        self._remove_legacy_cognition_files()
        for root in (
            self.entities_root, self.relationships_root, self.events_root,
            self.locations_root, self.concepts_root, self.definitions_root,
            self.knowledge_root, self.timeline_root,
        ):
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)

        m = {
            "schema_version": self.SCHEMA_VERSION,
            "project_id": self.project_id,
            "project_type": "domain",
            "current_version": 0,
            "artifacts": {},
            "entities": {},
            "counts": {},
            "timelines": [],
            "compiled": False,
        }
        self._write_master(m)
        self._db_reset()
        self._document_index = None
        return True

    def _db_reset(self):
        db = self.db
        with db.conn() as conn:
            for table in ("entity_tags", "sections", "attribute_states", "relations", "events", "source_chunks", "entities", "artifacts"):
                conn.execute(f"DELETE FROM {table}")

    def master_metadata(self):
        self.ensure_initialized()
        return self._read_json(self.master_metadata_path, {})

    def _kind_root(self, kind: str) -> Path:
        mapping = {
            "entity": self.entities_root,
            "entities": self.entities_root,
            "relationship": self.relationships_root,
            "relationships": self.relationships_root,
            "event": self.events_root,
            "events": self.events_root,
            "location": self.locations_root,
            "locations": self.locations_root,
            "concept": self.concepts_root,
            "concepts": self.concepts_root,
            "definition": self.definitions_root,
            "definitions": self.definitions_root,
            "knowledge": self.knowledge_root,
        }
        if kind not in mapping:
            raise ValueError(f"Unknown cognition kind: {kind}")
        return mapping[kind]

    def _object_path(self, kind: str, ident: str, subtype: str | None = None) -> Path:
        root = self._kind_root(kind)
        # Canonical entity objects live directly under cognition/entities/.
        # Type is a field, not another storage hierarchy.
        return root / f"{slug(ident)}.json"

    def _read_object(self, kind: str, ident: str):
        path = self._object_path(kind, str(ident))
        return self._read_json(path, {})

    @staticmethod
    def _append_text(existing: str, addition: str) -> str:
        addition = str(addition or "").strip()
        existing = str(existing or "").strip()
        if not addition:
            return existing
        # Treat paragraphs as atomic knowledge units. Exact repeats are ignored,
        # but later source-derived additions are never allowed to replace earlier ones.
        old = [x.strip() for x in existing.split("\n\n") if x.strip()]
        new = [x.strip() for x in addition.split("\n\n") if x.strip()]
        seen = {re.sub(r"\s+", " ", x).casefold() for x in old}
        for paragraph in new:
            key = re.sub(r"\s+", " ", paragraph).casefold()
            if key not in seen:
                old.append(paragraph)
                seen.add(key)
        return "\n\n".join(old)

    @staticmethod
    def _append_unique(items: list, additions: list, key_fields=("id",)) -> list:
        out = list(items or [])
        existing = set()
        for item in out:
            if isinstance(item, dict):
                existing.add(tuple(item.get(k) for k in key_fields))
            else:
                existing.add((item,))
        for item in additions or []:
            if not isinstance(item, dict):
                item = {"id": str(item)}
            key = tuple(item.get(k) for k in key_fields)
            if key not in existing:
                out.append(item)
                existing.add(key)
        return out

    def register_artifact(self, area, path, summary="", description="", artifact_type=None):
        if area not in ("source", "workspace"):
            raise ValueError(f"Invalid artifact area: {area!r}")
        root = self.source if area == "source" else self.workspace
        p = (root / path).resolve()
        p.relative_to(root.resolve())
        if not p.is_file():
            raise FileNotFoundError(f"Artifact does not exist: {p}")
        stat = p.stat()
        rec = {
            "id": f"{area}:{p.relative_to(root).as_posix()}",
            "area": area,
            "path": p.relative_to(root).as_posix(),
            "name": p.name,
            "type": artifact_type or p.suffix.lower().lstrip(".") or "file",
            "summary": summary,
            "description": description,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        }
        self.db.upsert_artifact(rec)
        m = self.master_metadata()
        m.setdefault("artifacts", {})[rec["id"]] = rec
        self._write_master(m)
        return rec

    def _ensure_entity(self, eid, etype="other", name=None):
        eid = str(eid)
        existing_meta = self.master_metadata().get("entities", {}).get(eid)
        path = Path(existing_meta["path"]) if existing_meta and existing_meta.get("path") else self._object_path("entity", eid, etype).relative_to(self.root)
        full = self.root / path
        data = self._read_json(full, {})
        if not data:
            data = {
                "schema_version": self.SCHEMA_VERSION,
                "id": eid,
                "type": etype or "other",
                "name": name or eid,
                "description": "",
                "summary": "",
                "tags": [],
                "knowledge": {},
                "attributes": {},
                "relationships": [],
                "events": [],
                "locations": [],
                "concepts": [],
                "definitions": [],
                "knowledge_links": [],
                "timeline": [],
                "provenance": [],
                "created_at": now_iso(),
            }
        data["type"] = etype or data.get("type", "other")
        if name:
            data["name"] = name
        data.setdefault("description", "")
        data.setdefault("summary", "")
        data.setdefault("tags", [])
        data.setdefault("knowledge", {})
        data.setdefault("attributes", {})
        data.setdefault("relationships", [])
        data.setdefault("events", [])
        data.setdefault("locations", [])
        data.setdefault("concepts", [])
        data.setdefault("definitions", [])
        data.setdefault("knowledge_links", [])
        data.setdefault("timeline", [])
        data.setdefault("provenance", [])
        data["updated_at"] = now_iso()
        self._write_json(full, data)

        m = self.master_metadata()
        m.setdefault("entities", {})[eid] = {
            "id": eid,
            "type": data["type"],
            "name": data.get("name", eid),
            "summary": data.get("summary", ""),
            "tags": data.get("tags", []),
            "path": path.as_posix(),
        }
        self._write_master(m)
        self.db.upsert_entity({**m["entities"][eid], "description": data.get("description", "")})
        return data

    def _record_provenance(self, data: dict, provenance):
        if not provenance:
            return
        entries = data.setdefault("provenance", [])
        for item in provenance if isinstance(provenance, list) else [provenance]:
            if not isinstance(item, dict):
                continue
            signature = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if not any(json.dumps(x, sort_keys=True, ensure_ascii=False) == signature for x in entries):
                entries.append(item)

    def _append_knowledge(self, data: dict, knowledge):
        if not isinstance(knowledge, dict):
            return
        target = data.setdefault("knowledge", {})
        for section, values in knowledge.items():
            if values is None:
                continue
            vals = values if isinstance(values, list) else [values]
            bucket = target.setdefault(str(section), [])
            seen = {json.dumps(x, sort_keys=True, ensure_ascii=False) for x in bucket}
            for value in vals:
                if isinstance(value, str):
                    value = {"text": value}
                if not isinstance(value, dict):
                    value = {"value": value}
                sig = json.dumps(value, sort_keys=True, ensure_ascii=False)
                if sig not in seen:
                    bucket.append(value)
                    seen.add(sig)

    def _append_attribute(self, data: dict, attr: str, value, timeline=None, source_artifact=None, locator=None, summary="", sequence=None, valid_from=None, valid_to=None, event_id=None):
        states = data.setdefault("attributes", {}).setdefault(str(attr), [])
        sig = json.dumps({"value": value, "timeline": timeline, "source_artifact": source_artifact, "locator": locator, "event_id": event_id}, sort_keys=True, ensure_ascii=False)
        if any(json.dumps({"value": s.get("value"), "timeline": s.get("timeline"), "source_artifact": s.get("source_artifact"), "locator": s.get("source_locator"), "event_id": s.get("event_id")}, sort_keys=True, ensure_ascii=False) == sig for s in states):
            return
        rec = {
            "value": value,
            "summary": summary,
            "timeline": timeline,
            "timeline_sequence": sequence if sequence is not None else _timeline_rank(timeline),
            "valid_from": valid_from or timeline,
            "valid_to": valid_to,
            "event_id": event_id,
            "source_artifact": source_artifact,
            "source_locator": locator,
            "recorded_at": now_iso(),
            "sequence": len(states) + 1,
        }
        states.append(rec)

    def upsert_entity_update(self, update, source_artifact=None, chunk_index=0, default_timeline=None, default_sequence=None):
        eid = str(update.get("id") or update.get("stable_id") or update.get("name") or "")
        if not eid:
            raise ValueError("entity update requires id")
        data = self._ensure_entity(eid, update.get("type", "other"), update.get("name") or eid)
        if update.get("name"):
            data["name"] = update["name"]
        if update.get("type"):
            data["type"] = update["type"]
        # Never replace durable character/entity prose with the latest chunk.
        description_addition = update.get("description") or update.get("summary") or ""
        data["description"] = self._append_text(data.get("description", ""), description_addition)
        if not data.get("summary") and description_addition:
            data["summary"] = description_addition.strip().split("\n", 1)[0][:500]
        for tag in update.get("tags") or []:
            if str(tag) not in data["tags"]:
                data["tags"].append(str(tag))
        self._append_knowledge(data, update.get("knowledge"))
        timeline = update.get("timeline") or default_timeline
        provenance = update.get("provenance") or update.get("locations") or []
        self._record_provenance(data, provenance)
        for attr, value in (update.get("attributes") or {}).items():
            if isinstance(value, dict):
                self._append_attribute(
                    data, attr, value.get("value", value.get("state")),
                    timeline=value.get("timeline", timeline), source_artifact=source_artifact,
                    locator=value.get("locator"), summary=value.get("summary", ""),
                    sequence=value.get("sequence", default_sequence), valid_from=value.get("valid_from"),
                    valid_to=value.get("valid_to"), event_id=value.get("event_id") or value.get("valid_from_event"),
                )
            else:
                self._append_attribute(data, attr, value, timeline, source_artifact, None, "", default_sequence)
        for ref_kind in ("relationships", "events", "locations", "concepts", "definitions", "knowledge_links"):
            refs = []
            for item in update.get(ref_kind) or []:
                if isinstance(item, str):
                    refs.append({"id": item})
                elif isinstance(item, dict) and item.get("id"):
                    refs.append({"id": str(item["id"])})
            data[ref_kind] = self._append_unique(data.get(ref_kind, []), refs)
        if update.get("timeline_entry"):
            data["timeline"] = self._append_unique(data.get("timeline", []), [update["timeline_entry"]])
        if source_artifact:
            prov = {
                "artifact_id": source_artifact,
                "chunk_index": chunk_index,
                "timeline": timeline,
            }
            if update.get("locations"):
                prov["locations"] = update["locations"]
            self._record_provenance(data, [prov])
        data["updated_at"] = now_iso()
        meta = self.master_metadata()["entities"][eid]
        path = self.root / meta["path"]
        self._write_json(path, data)
        m = self.master_metadata()
        m["entities"][eid].update({"type": data["type"], "name": data["name"], "summary": data.get("summary", ""), "tags": data.get("tags", [])})
        self._write_master(m)
        self.db.sync_entity_index(self, eid)
        if description_addition or update.get("knowledge") or update.get("attributes"):
            timeline_entry = {"sequence": default_sequence, "timeline": timeline, "kind": "entity_update", "object_id": eid}
            data["timeline"] = self._append_unique(data.get("timeline", []), [timeline_entry])
            self._write_json(path, data)
            self.append_timeline_entry(timeline or "document", timeline_entry)
        return eid

    def _canonical_upsert(self, kind: str, record: dict, source_artifact=None, timeline=None, location=None):
        ident = str(record.get("id") or record.get("stable_id") or "")
        if not ident:
            raise ValueError(f"{kind} requires id")
        path = self._object_path(kind, ident)
        existing = self._read_json(path, {})
        rec = dict(existing)
        for key, value in record.items():
            if value is None:
                continue
            if isinstance(value, list) and isinstance(existing.get(key), list):
                merged = list(existing.get(key) or [])
                for item in value:
                    if item not in merged:
                        merged.append(item)
                rec[key] = merged
            else:
                rec[key] = value
        rec["schema_version"] = self.SCHEMA_VERSION
        rec["id"] = ident
        rec["created_at"] = existing.get("created_at", now_iso())
        rec["updated_at"] = now_iso()
        rec.setdefault("description", "")
        rec.setdefault("relationships", [])
        rec.setdefault("evolution", [])
        addition = record.get("description", "")
        rec["description"] = self._append_text(existing.get("description", ""), addition)
        if addition:
            observation = {
                "sequence": len(rec["evolution"]) + 1,
                "timeline": timeline,
                "description": addition,
                "source_artifact": source_artifact,
                "locator": location,
                "recorded_at": now_iso(),
            }
            sig = json.dumps({k: v for k, v in observation.items() if k != "recorded_at"}, sort_keys=True, ensure_ascii=False)
            if not any(json.dumps({k: v for k, v in x.items() if k != "recorded_at"}, sort_keys=True, ensure_ascii=False) == sig for x in rec["evolution"]):
                rec["evolution"].append(observation)
        if source_artifact:
            rec.setdefault("provenance", [])
            prov = {
                "artifact_id": source_artifact,
                "timeline": timeline,
            }
            if location:
                prov["locator"] = location
            self._record_provenance(rec, [prov])
        if location:
            rec.setdefault("source_locations", [])
            self._record_provenance(rec, [{"artifact_id": source_artifact, "locator": location}])
        self._write_json(path, rec)
        if addition:
            self.append_timeline_entry(timeline or "document", {"sequence": len(rec["evolution"]), "kind": kind, "object_id": ident})
        return rec

    def upsert_canonical(self, kind, record, source_artifact=None, timeline=None, location=None):
        """Append/update one canonical non-entity cognition object."""
        return self._canonical_upsert(kind, record, source_artifact=source_artifact, timeline=timeline, location=location)

    def record_state_change(self, entity_id, attribute, previous_value, new_value, description, event_id, timeline, sequence, source_artifact, locator):
        """Append a temporal state observation to an entity and its timeline."""
        entity_id = str(entity_id)
        if entity_id not in self.master_metadata().get("entities", {}):
            return False
        path = self._entity_path(entity_id)
        data = self._read_json(path, {})
        self._append_attribute(data, attribute, new_value, timeline=timeline, source_artifact=source_artifact, locator=locator, summary=description, sequence=sequence, valid_from=timeline, event_id=event_id)
        entry = {"sequence": sequence, "timeline": timeline, "kind": "state_change", "object_id": event_id, "entity_ids": [entity_id], "attribute": attribute}
        data["timeline"] = self._append_unique(data.get("timeline", []), [entry])
        self._record_provenance(data, [{"artifact_id": source_artifact, "locator": locator, "event_id": event_id}])
        data["updated_at"] = now_iso()
        self._write_json(path, data)
        self.db.sync_entity_index(self, entity_id)
        self.append_timeline_entry(timeline, entry)
        return True

    def add_relationship(self, rel, default_source=None, source_artifact=None, timeline=None, data=None):
        if not isinstance(rel, dict):
            return None
        source = str(rel.get("source") or rel.get("from") or default_source or "")
        target = str(rel.get("target") or rel.get("to") or "")
        if not source or not target:
            return None
        rid = str(rel.get("id") or f"relationship:{source}:{rel.get('type','related_to')}:{target}")
        existing = self._read_object("relationship", rid)
        rec = dict(existing)
        rec.update({
            "schema_version": self.SCHEMA_VERSION,
            "id": rid,
            "type": rel.get("type") or rel.get("relation_type") or existing.get("type", "related_to"),
            "source": source,
            "target": target,
            "source_kind": rel.get("source_kind", existing.get("source_kind", "entity")),
            "target_kind": rel.get("target_kind", existing.get("target_kind", "entity")),
            "source_ref": {"kind": rel.get("source_kind", existing.get("source_kind", "entity")), "id": source},
            "target_ref": {"kind": rel.get("target_kind", existing.get("target_kind", "entity")), "id": target},
            "state": rel.get("state", existing.get("state")),
            "description": self._append_text(existing.get("description", ""), rel.get("description") or rel.get("state") or ""),
            "timeline": rel.get("timeline", timeline),
            "evolution": list(existing.get("evolution") or []),
            "provenance": list(existing.get("provenance") or []),
            "created_at": existing.get("created_at", now_iso()),
            "updated_at": now_iso(),
        })
        observation = {
            "sequence": len(rec["evolution"]) + 1,
            "timeline": rel.get("timeline", timeline),
            "state": rel.get("state"),
            "description": rel.get("description") or rel.get("state") or "",
            "source_artifact": source_artifact,
            "source_locations": rel.get("document_locations") or [],
            "recorded_at": now_iso(),
        }
        sig = json.dumps({k: v for k, v in observation.items() if k != "recorded_at"}, sort_keys=True, ensure_ascii=False)
        if not any(json.dumps({k: v for k, v in x.items() if k != "recorded_at"}, sort_keys=True, ensure_ascii=False) == sig for x in rec["evolution"]):
            rec["evolution"].append(observation)
        if source_artifact:
            self._record_provenance(rec, [{"artifact_id": source_artifact, "locator": (rel.get("document_locations") or [])}])
        self._write_json(self._object_path("relationship", rid), rec)
        self.db.add_relation({"id": rid, "from_id": source, "to_id": target, "relation_type": rec["type"], "description": rec["description"], "timeline": rec.get("timeline"), "source_artifact": source_artifact})
        source_kind = rec.get("source_kind", "entity")
        target_kind = rec.get("target_kind", "entity")
        self._link_object_reference(source_kind, source, "relationships", rid, as_object=True)
        self._link_object_reference(target_kind, target, "relationships", rid, as_object=True)
        for loc in rel.get("document_locations") or []:
            self._attach_location("relationship", rid, loc, source_artifact)
        return rid

    def _link_object_reference(self, kind: str, ident: str, field: str, ref_id: str, as_object: bool = False):
        if kind == "entity":
            path = self._entity_path(ident)
        else:
            path = self._object_path(kind, ident)
        data = self._read_json(path, {})
        if not data:
            return
        refs = data.setdefault(field, [])
        value = {"id": str(ref_id)} if as_object else str(ref_id)
        if value not in refs:
            refs.append(value)
            data["updated_at"] = now_iso()
            self._write_json(path, data)

    def _link_reference(self, kind: str, ident: str, field: str, ref_id: str):
        if kind == "entity":
            path = self.root / self.master_metadata()["entities"][str(ident)]["path"]
        else:
            path = self._object_path(kind, ident)
        data = self._read_json(path, {})
        if not data:
            return
        refs = data.setdefault(field, [])
        if not any(isinstance(x, dict) and x.get("id") == ref_id for x in refs):
            refs.append({"id": ref_id})
            data["updated_at"] = now_iso()
            self._write_json(path, data)

    def _event_path(self, event_id):
        return self._object_path("event", event_id)

    def add_event(self, event, source_artifact=None, timeline=None, default_entity=None, data=None, sequence=None, narrative_position=None):
        if not isinstance(event, dict):
            return None
        entities = [str(x) for x in (event.get("entities") or ([default_entity] if default_entity else [])) if x]
        event_id = str(event.get("id") or event.get("event_id") or "")
        if not event_id:
            raise ValueError("Every canonical event must have a stable id")
        existing = self._read_object("event", event_id)
        pos = dict(existing.get("narrative_position") or {})
        if isinstance(narrative_position, dict):
            pos.update(narrative_position)
        if isinstance(event.get("narrative_position"), dict):
            pos.update(event["narrative_position"])
        if timeline and not pos.get("label"):
            pos["label"] = timeline
        if sequence is not None and pos.get("sequence") is None:
            pos["sequence"] = sequence
        rec = dict(existing)
        rec.update({
            "schema_version": self.SCHEMA_VERSION,
            "id": event_id,
            "type": event.get("type") or existing.get("type", "event"),
            "title": event.get("title") or existing.get("title", event_id),
            "description": self._append_text(existing.get("description", ""), event.get("description") or event.get("event") or ""),
            "entities": list(dict.fromkeys((existing.get("entities") or []) + entities)),
            "location_ids": list(dict.fromkeys((existing.get("location_ids") or []) + [str(x) for x in (event.get("location_ids") or [])])),
            "concept_ids": list(dict.fromkeys((existing.get("concept_ids") or []) + [str(x) for x in (event.get("concept_ids") or [])])),
            "narrative_position": pos,
            "sequence": event.get("sequence", existing.get("sequence", sequence)),
            "previous_events": list(dict.fromkeys((existing.get("previous_events") or []) + [str(x) for x in (event.get("previous_events") or [])])),
            "next_events": list(dict.fromkeys((existing.get("next_events") or []) + [str(x) for x in (event.get("next_events") or [])])),
            "evolution": list(existing.get("evolution") or []),
            "provenance": list(existing.get("provenance") or []),
            "created_at": existing.get("created_at", now_iso()),
            "updated_at": now_iso(),
        })
        observation = {
            "sequence": len(rec["evolution"]) + 1,
            "timeline": pos.get("label") or timeline,
            "story_time": pos.get("story_time"),
            "description": event.get("description") or event.get("event") or "",
            "state_changes": event.get("state_changes") or [],
            "source_artifact": source_artifact,
            "source_locations": event.get("document_locations") or [],
            "recorded_at": now_iso(),
        }
        sig = json.dumps({k: v for k, v in observation.items() if k != "recorded_at"}, sort_keys=True, ensure_ascii=False)
        if observation["description"] and not any(json.dumps({k: v for k, v in x.items() if k != "recorded_at"}, sort_keys=True, ensure_ascii=False) == sig for x in rec["evolution"]):
            rec["evolution"].append(observation)
        if source_artifact:
            self._record_provenance(rec, [{"artifact_id": source_artifact, "locator": event.get("source_locator"), "locations": event.get("document_locations") or []}])
        self._write_json(self._event_path(event_id), rec)
        self.db.add_event({"id": event_id, "description": rec["description"], "entities": rec["entities"], "timeline": pos.get("label") or timeline, "source_artifact": source_artifact, "source_locator": json.dumps(event.get("source_locator"), ensure_ascii=False) if isinstance(event.get("source_locator"), (dict, list)) else event.get("source_locator"), "created_at": rec["created_at"]})
        for eid in rec["entities"]:
            if eid in self.master_metadata().get("entities", {}):
                self._link_reference("entity", eid, "events", event_id)
                entity_path = self._entity_path(eid)
                entity_data = self._read_json(entity_path, {})
                if entity_data:
                    entity_entry = {
                        "sequence": pos.get("sequence", sequence),
                        "timeline": pos.get("label") or timeline,
                        "story_time": pos.get("story_time"),
                        "kind": "event",
                        "object_id": event_id,
                    }
                    entity_data["timeline"] = self._append_unique(entity_data.get("timeline", []), [entity_entry])
                    entity_data["updated_at"] = now_iso()
                    self._write_json(entity_path, entity_data)
        for lid in rec.get("location_ids", []):
            self._link_object_reference("location", lid, "event_ids", event_id, as_object=False)
        for cid in rec.get("concept_ids", []):
            self._link_object_reference("concept", cid, "event_ids", event_id, as_object=False)
        for loc in event.get("document_locations") or []:
            self._attach_location("event", event_id, loc, source_artifact)
        self.append_timeline_entry(pos.get("label") or timeline or "document", {
            "sequence": pos.get("sequence", sequence),
            "story_time": pos.get("story_time"),
            "kind": "event",
            "object_id": event_id,
            "entity_ids": rec["entities"],
        })
        return event_id

    def _attach_location(self, kind, ident, loc, source_artifact=None):
        try:
            self.document_index.attach(kind, ident, loc.get("artifact_id") or source_artifact, node_ids=loc.get("node_ids"), segment_id=loc.get("segment_id"), locator=loc.get("locator"), section_ids=loc.get("section_ids"), chapter_id=loc.get("chapter_id"), scene_id=loc.get("scene_id"))
        except Exception:
            pass

    def append_timeline_entry(self, timeline_id, entry):
        """Append a reference-only entry to the canonical global timeline.

        The timeline never duplicates event/entity descriptions. It records
        ordering and references so the state of an object can be reconstructed
        without maintaining a second copy of the underlying cognition.
        """
        timeline_id = str(timeline_id or "document")
        global_path = self.timeline_root / "timeline.json"
        existing = self._read_json(global_path, {"schema_version": self.SCHEMA_VERSION, "id": "timeline", "entries": []})
        entry = dict(entry or {})
        entry.setdefault("timeline", timeline_id)
        entries = existing.setdefault("entries", [])
        sig = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        if not any(json.dumps(x, sort_keys=True, ensure_ascii=False) == sig for x in entries):
            entries.append(entry)
        entries.sort(key=lambda x: ((x.get("sequence") is None), x.get("sequence") or 10**12, str(x.get("timeline") or ""), str(x.get("story_time") or ""), str(x.get("object_id") or "")))
        existing["updated_at"] = now_iso()
        self._write_json(global_path, existing)

        m = self.master_metadata()
        timelines = set(m.get("timelines") or [])
        timelines.add(timeline_id)
        m["timelines"] = sorted(timelines)
        self._write_master(m)

    def link_event_to_entity(self, entity_id, event_id):
        if str(entity_id) not in self.master_metadata().get("entities", {}):
            return
        self._link_reference("entity", str(entity_id), "events", str(event_id))

    def events(self, limit=1000):
        if not self.events_root.exists():
            return []
        records = []
        for path in self.events_root.glob("*.json"):
            rec = self._read_json(path, {})
            if rec:
                records.append(rec)
        records.sort(key=lambda x: (x.get("sequence") is None, x.get("sequence") or 10**12, str(x.get("created_at", "")), str(x.get("id", ""))))
        return records[:limit]

    def event(self, event_id):
        return self._read_json(self._event_path(str(event_id)), {})

    def relationships(self):
        if not self.relationships_root.exists():
            return {}
        out = {}
        for path in self.relationships_root.glob("*.json"):
            rec = self._read_json(path, {})
            if rec:
                out[str(rec.get("id") or path.stem)] = rec
        return out

    def relationship_history(self, relationship_id=None):
        rels = self.relationships()
        if relationship_id is None:
            return {rid: rec.get("evolution", []) for rid, rec in rels.items()}
        return rels.get(str(relationship_id), {}).get("evolution", [])

    def _entity_path(self, entity_id):
        meta = self.master_metadata().get("entities", {}).get(str(entity_id))
        return self.root / meta["path"] if meta and meta.get("path") else None

    def _read_entity(self, entity_id):
        path = self._entity_path(entity_id)
        return self._read_json(path, {}) if path else {}

    def entity(self, name):
        return self._read_entity(name)

    def load_entities(self, names):
        out = {"entities": {}, "relationships": {}, "events": {}, "locations": {}, "missing": []}
        for name in names or []:
            ident = str(name)
            if ident not in self.master_metadata().get("entities", {}):
                # Exact name lookup.
                found = next((eid for eid, meta in self.master_metadata().get("entities", {}).items() if str(meta.get("name", "")).casefold() == ident.casefold()), None)
                ident = found or ident
            if ident not in self.master_metadata().get("entities", {}):
                out["missing"].append(name)
                continue
            entity = self._read_entity(ident)
            out["entities"][ident] = entity
            for ref in entity.get("relationships", []):
                rid = ref.get("id") if isinstance(ref, dict) else ref
                if rid in self.relationships():
                    out["relationships"][rid] = self.relationships()[rid]
            for ref in entity.get("events", []):
                eid = ref.get("id") if isinstance(ref, dict) else ref
                ev = self.event(eid)
                if ev:
                    out["events"][eid] = ev
            for ref in entity.get("locations", []):
                lid = ref.get("id") if isinstance(ref, dict) else ref
                loc = self._read_object("location", lid)
                if loc:
                    out["locations"][lid] = loc
        return out

    def matching_entities(self, text):
        tokens = set(re.findall(r"[a-zA-Z0-9_'-]+", str(text).lower()))
        if not tokens:
            return []
        matches = []
        for eid, meta in self.master_metadata().get("entities", {}).items():
            hay = " ".join([str(meta.get("name", "")), str(meta.get("summary", "")), " ".join(meta.get("tags", []))]).lower()
            score = sum(1 for t in tokens if t in hay)
            if score:
                matches.append((score, eid))
        return [eid for _, eid in sorted(matches, key=lambda x: (-x[0], x[1]))[:50]]

    def summaries(self):
        return {eid: meta.get("summary", "") for eid, meta in self.master_metadata().get("entities", {}).items()}

    def state_map(self):
        # Computed view only; no cognition/state_map.json is written.
        m = self.master_metadata()
        return {
            "project_id": self.project_id,
            "type": m.get("project_type", "domain"),
            "entities": m.get("entities", {}),
            "timeline": m.get("timelines", []),
            "current_version": m.get("current_version", 0),
            "compiled": bool(m.get("compiled")),
        }

    def ledger(self):
        out = {}
        for eid in self.master_metadata().get("entities", {}):
            entity = self._read_entity(eid)
            out[eid] = {attr: states[-1].get("value") for attr, states in (entity.get("attributes") or {}).items() if states}
        return out

    def retrieval_index(self):
        m = self.master_metadata()
        return {
            "project_id": self.project_id,
            "project_type": m.get("project_type"),
            "current_version": m.get("current_version", 0),
            "entities": list(m.get("entities", {}).values()),
            "artifacts": list(m.get("artifacts", {}).values()),
            "counts": m.get("counts", {}),
            "document_index": self.document_index.retrieval_index(limit=100),
        }

    def _all_kind_records(self, kind):
        root = self._kind_root(kind)
        if not root.exists():
            return []
        return [rec for p in root.rglob("*.json") if (rec := self._read_json(p, {}))]

    def _search_canonical(self, query, limit=20):
        q = str(query or "").strip().casefold()
        tokens = [t for t in re.findall(r"[a-zA-Z0-9_'-]+", q) if t]
        candidates = []
        for kind in ("entity", "relationship", "event", "location", "concept", "definition", "knowledge"):
            for rec in self._all_kind_records(kind):
                text = " ".join(str(rec.get(k, "")) for k in ("id", "name", "title", "description", "summary", "type", "state"))
                low = text.casefold()
                phrase = 8 if q and q in low else 0
                score = phrase + sum(2 for t in tokens if t in low)
                if score:
                    candidates.append((score, kind, rec))
        candidates.sort(key=lambda x: (-x[0], x[1], str(x[2].get("name") or x[2].get("title") or x[2].get("id"))))
        out = []
        seen = set()
        for score, kind, rec in candidates:
            ident = str(rec.get("id"))
            if (kind, ident) in seen:
                continue
            seen.add((kind, ident))
            out.append({"id": ident, "kind": kind, "type": rec.get("type"), "name": rec.get("name") or rec.get("title") or ident, "summary": (rec.get("summary") or rec.get("description") or "")[:1000], "provenance": rec.get("provenance", [])[:3] if isinstance(rec.get("provenance"), list) else []})
            if len(out) >= limit:
                break
        return out

    def search_cognition_metadata(self, query, limit=8):
        return {"query": query, "candidates": self._search_canonical(query, max(1, min(int(limit or 8), 50))), "source_loaded": False}

    def retrieve(self, requests, max_chars=16000):
        result = {"requests": []}
        used = 0
        for req in requests or []:
            detail = str(req.get("detail", "summary")).lower()
            ids = []
            if req.get("entity_id"):
                ids = [str(req["entity_id"])]
            elif req.get("entity_ids"):
                ids = [str(x) for x in req["entity_ids"]]
            elif req.get("name") or req.get("query"):
                ids = self.matching_entities(req.get("name") or req.get("query"))
            for eid in ids[:20]:
                entity = self._read_entity(eid)
                if not entity:
                    continue
                item = {"id": eid, "type": entity.get("type"), "name": entity.get("name"), "summary": entity.get("summary", ""), "description": entity.get("description", ""), "tags": entity.get("tags", [])}
                if detail in {"state", "section", "full"}:
                    item["knowledge"] = entity.get("knowledge", {})
                    item["attributes"] = entity.get("attributes", {})
                    item["provenance"] = entity.get("provenance", [])
                    item["timeline"] = entity.get("timeline", [])
                if detail in {"full", "section"}:
                    item["relationship_refs"] = entity.get("relationships", [])
                    item["event_refs"] = entity.get("events", [])
                    item["location_refs"] = entity.get("locations", [])
                    item["concept_refs"] = entity.get("concepts", [])
                if detail == "full":
                    item["relationships"] = [self.relationships().get(r.get("id")) for r in entity.get("relationships", []) if isinstance(r, dict) and self.relationships().get(r.get("id"))]
                    item["events"] = [self.event(r.get("id")) for r in entity.get("events", []) if isinstance(r, dict) and self.event(r.get("id"))]
                    item["locations"] = [self._read_object("location", r.get("id")) for r in entity.get("locations", []) if isinstance(r, dict) and self._read_object("location", r.get("id"))]
                payload = json.dumps(item, ensure_ascii=False)
                if used + len(payload) > max_chars:
                    break
                result["requests"].append(item)
                used += len(payload)
        result["used_chars"] = used
        result["truncated"] = used >= max_chars
        return result

    def document_structure(self, artifact_id=None, query=None, limit=200):
        if artifact_id:
            return {"document": self.document_index.document(artifact_id), "sections": self.document_index.sections(artifact_id=artifact_id, query=query, limit=limit)}
        return self.document_index.retrieval_index(limit=limit)

    def resolve_document_section(self, query, artifact_id=None):
        section = self.document_index.resolve_section(query, artifact_id=artifact_id)
        return {"found": bool(section), "query": query, "section": section} if section else {"found": False, "query": query}

    def cognition_locations(self, kind, ident):
        return self.document_index.locations(kind, ident)

    def read_source_location(self, artifact_id, locator):
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
            reader = PdfReader(str(path))
            start = max(1, int(loc.get("page_start") or loc.get("page") or 1))
            end = min(len(reader.pages), int(loc.get("page_end") or loc.get("page") or start))
            pages = []
            for page_no in range(start, end + 1):
                text = reader.pages[page_no - 1].extract_text() or ""
                lines = text.splitlines()
                lo = int(loc.get("line_start", 0)) if page_no == start else 0
                hi = int(loc.get("line_end", len(lines) - 1)) if page_no == end else len(lines) - 1
                text = "\n".join(lines[max(0, lo):min(len(lines), hi + 1)])
                pages.append({"page": page_no, "text": text})
            return {"artifact_id": artifact_id, "path": artifact.get("path"), "locator": loc, "pages": pages}
        if ext == ".docx":
            from docx import Document
            doc = Document(str(path))
            start = int(loc.get("paragraph_start", loc.get("paragraph_index", 0)))
            end = int(loc.get("paragraph_end", start))
            end = min(max(0, end), max(0, len(doc.paragraphs) - 1))
            return {"artifact_id": artifact_id, "path": artifact.get("path"), "locator": loc, "paragraphs": [{"index": i, "text": doc.paragraphs[i].text, "style": doc.paragraphs[i].style.name if doc.paragraphs[i].style else None} for i in range(max(0, start), end + 1)]}
        if ext == ".pptx":
            from pptx import Presentation
            prs = Presentation(str(path))
            start = int(loc.get("slide_start", loc.get("slide", 1)))
            end = min(len(prs.slides), int(loc.get("slide_end", loc.get("slide", start))))
            return {"artifact_id": artifact_id, "path": artifact.get("path"), "locator": loc, "slides": [{"slide": i, "shapes": [{"shape_index": j, "text": s.text} for j, s in enumerate(prs.slides[i - 1].shapes) if getattr(s, "has_text_frame", False) and s.text]} for i in range(start, end + 1)]}
        if ext == ".xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True, data_only=False)
            ws = wb[loc.get("sheet")] if loc.get("sheet") in wb.sheetnames else wb[wb.sheetnames[0]]
            start = int(loc.get("row_start", loc.get("row", 1)))
            end = int(loc.get("row_end", loc.get("row", start)))
            return {"artifact_id": artifact_id, "path": artifact.get("path"), "locator": loc, "sheet": ws.title, "rows": [[c.value for c in row] for row in ws.iter_rows(min_row=start, max_row=end)]}
        text = path.read_text(encoding="utf-8", errors="replace")
        start = int(loc.get("char_start", 0))
        end = int(loc.get("char_end", min(len(text), start + 12000)))
        return {"artifact_id": artifact_id, "path": artifact.get("path"), "locator": loc, "text": text[start:end]}

    def expand_source_artifacts(self, source_artifacts):
        """Return all source artifacts that contribute to objects touched by the seeds."""
        expanded = {str(x) for x in (source_artifacts or [])}
        changed = True
        while changed:
            changed = False
            for kind in ("entity", "relationship", "event", "location", "concept", "definition", "knowledge"):
                for rec in self._all_kind_records(kind):
                    prov = rec.get("provenance") or []
                    aids = {str(p.get("artifact_id")) for p in prov if isinstance(p, dict) and p.get("artifact_id")}
                    if aids & expanded and not aids.issubset(expanded):
                        expanded.update(aids)
                        changed = True
        return expanded

    def remove_source_projection(self, source_artifacts):
        """Remove canonical objects supported only by selected source artifacts."""
        source_artifacts = {str(x) for x in (source_artifacts or [])}
        if not source_artifacts:
            return
        # Objects are source-derived unless explicitly marked otherwise. Remove
        # objects whose entire provenance set belongs to the replaced artifacts.
        removed_ids = set()
        for kind in ("entity", "relationship", "event", "location", "concept", "definition", "knowledge"):
            for rec in list(self._all_kind_records(kind)):
                prov = rec.get("provenance") or []
                aids = {str(p.get("artifact_id")) for p in prov if isinstance(p, dict) and p.get("artifact_id")}
                if aids and aids.issubset(source_artifacts):
                    removed_ids.add(str(rec["id"]))
                    self._object_path(kind, rec["id"]).unlink(missing_ok=True)
        # Rebuild master entity metadata from surviving entity files.
        m = self.master_metadata()
        m["entities"] = {}
        for rec in self._all_kind_records("entity"):
            path = self._object_path("entity", rec["id"])
            m["entities"][rec["id"]] = {"id": rec["id"], "type": rec.get("type", "other"), "name": rec.get("name", rec["id"]), "summary": rec.get("summary", ""), "tags": rec.get("tags", []), "path": path.relative_to(self.root).as_posix()}
        for aid in source_artifacts:
            try:
                self.document_index.remove_artifact(aid)
            except Exception:
                pass
            self.db.delete_relations_for_artifact(aid)
            self.db.delete_events_for_artifact(aid)
            self.db.delete_source_chunks_for_artifact(aid)
            self.db.delete_artifact(aid)
        timeline_path = self.timeline_root / "timeline.json"
        if timeline_path.exists() and removed_ids:
            timeline = self._read_json(timeline_path, {})
            timeline["entries"] = [e for e in timeline.get("entries", []) if str(e.get("object_id")) not in removed_ids]
            self._write_json(timeline_path, timeline)

        self._write_master(m)
        self.db.rebuild_entity_index(self)
        self.db.rebuild_event_index(self)

    def record_compilation_chunk(self, artifact, chunk_index, content=None, summary="", locator=None):
        aid = artifact.get("id") if isinstance(artifact, dict) else artifact
        self.db.add_source_chunk({"artifact_id": aid, "area": artifact.get("area") if isinstance(artifact, dict) else None, "path": artifact.get("path") if isinstance(artifact, dict) else str(artifact), "chunk_index": chunk_index, "locator": locator, "summary": summary, "content": ""})

    def set_project_type(self, value):
        m = self.master_metadata()
        if value:
            m["project_type"] = value
        self._write_master(m)

    def mark_compiled(self):
        m = self.master_metadata()
        m["current_version"] = int(m.get("current_version", 0)) + 1
        m["compiled"] = True
        m["counts"] = {
            "entities": len(self._all_kind_records("entity")),
            "relationships": len(self._all_kind_records("relationship")),
            "events": len(self._all_kind_records("event")),
            "locations": len(self._all_kind_records("location")),
            "concepts": len(self._all_kind_records("concept")),
            "definitions": len(self._all_kind_records("definition")),
            "knowledge": len(self._all_kind_records("knowledge")),
        }
        self._write_master(m)

    def initialize(self, *args, **kwargs):
        self.ensure_initialized()
        return self.master_metadata().get("current_version", 0)

    def snapshot(self, label="update"):
        self.mark_compiled()
        return self.master_metadata().get("current_version", 0)

    def append_event(self, event):
        # Runtime/audit events do not belong in canonical cognition. Keep them
        # outside cognition so they cannot be mistaken for source-derived events.
        path = self.system / "runtime_events.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": now_iso(), **(event or {})}, ensure_ascii=False) + "\n")

    def briefing(self, task):
        self.ensure_initialized()
        return {"project": {"project_id": self.project_id, "project_type": self.master_metadata().get("project_type"), "current_version": self.master_metadata().get("current_version", 0)}, "retrieval_instruction": "Use metadata search and canonical cognition first. Canonical cognition is complete derived knowledge; source reads are only for exact evidence/verification. Do not treat source pointers as cognition.", "matching_entities": [self.master_metadata()["entities"][x] for x in self.matching_entities(task)[:20]], "available_entities": list(self.master_metadata().get("entities", {}).values()), "document_index": self.document_structure(query=task, limit=20)}
