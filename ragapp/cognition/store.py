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


def _coerce_int(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_chapter_reference(value):
    """Extract a chapter/scene/part ordinal without treating it as story time."""
    if value is None:
        return None
    text = str(value).strip()
    patterns = (
        ("chapter", r"\bchapter\s*([0-9]+)\b"),
        ("scene", r"\bscene\s*([0-9]+)\b"),
        ("part", r"\bpart\s*([0-9]+)\b"),
    )
    for kind, pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return {"kind": kind, "ordinal": int(m.group(1)), "label": m.group(0)}
    return None


def _normalize_temporal_position(narrative=None, story_time=None, sequence=None):
    """Return the canonical two-axis temporal position.

    Narrative position answers where information entered the document/chat
    progression. Story time answers when the represented event/state exists in
    the story world. They are intentionally independent; narrative position is
    never used as a proxy for story time.
    """
    narrative = narrative if isinstance(narrative, dict) else {"label": narrative} if narrative is not None else {}
    story = story_time if isinstance(story_time, dict) else {"label": story_time} if story_time is not None else {}

    n = {
        "sequence": _coerce_int(narrative.get("sequence", sequence)),
        "label": narrative.get("label") or narrative.get("timeline"),
        "chapter": _coerce_int(narrative.get("chapter")),
        "scene": _coerce_int(narrative.get("scene")),
        "session": narrative.get("session"),
        "turn": _coerce_int(narrative.get("turn")),
    }
    if n["chapter"] is None:
        ref = _parse_chapter_reference(n.get("label"))
        if ref and ref["kind"] == "chapter":
            n["chapter"] = ref["ordinal"]
    if n["scene"] is None:
        ref = _parse_chapter_reference(n.get("label"))
        if ref and ref["kind"] == "scene":
            n["scene"] = ref["ordinal"]
    n = {k: v for k, v in n.items() if v is not None}

    s = {
        "label": story.get("label"),
        "kind": story.get("kind"),
        "ordinal": _coerce_int(story.get("ordinal")),
        "chapter": _coerce_int(story.get("chapter")),
        "scene": _coerce_int(story.get("scene")),
        "anchor": story.get("anchor"),
        "relation": story.get("relation"),
    }
    # A structured story-time input is authoritative. For a free-form string,
    # preserve it verbatim and only extract an explicit relation/anchor.
    if s.get("label") and not s.get("relation"):
        text = str(s["label"]).strip()
        m = re.search(r"\b(before|after|during|at|between)\b", text, re.I)
        if m:
            s["relation"] = m.group(1).lower()
        ref = _parse_chapter_reference(text)
        if ref:
            s.setdefault("kind", ref["kind"])
            s.setdefault("ordinal", ref["ordinal"])
            if ref["kind"] == "chapter":
                s.setdefault("chapter", ref["ordinal"])
            if s.get("anchor") is None:
                s["anchor"] = ref["label"]
    s = {k: v for k, v in s.items() if v is not None}

    return {
        "model_version": 2,
        "narrative": n,
        "story": s,
    }


class CognitionStore:
    """Filesystem-authoritative canonical cognition with SQLite as an index only."""

    SCHEMA_VERSION = 9
    TEMPORAL_MODEL_VERSION = 2
    TIMELINE_SCHEMA_VERSION = 2
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
            "temporal_model_version": self.TEMPORAL_MODEL_VERSION,
            "project_id": self.project_id,
            "project_type": "domain",
            "current_version": 0,
            "artifacts": {},
            "entities": {},
            "counts": {},
            "timelines": [],
            "temporal_model": {
                "version": self.TEMPORAL_MODEL_VERSION,
                "timeline_schema_version": self.TIMELINE_SCHEMA_VERSION,
                "axes": ["narrative", "story"],
                "narrative": "document/chat progression; independent from story chronology",
                "story": "in-story chronology; may diverge from narrative order",
            },
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
            "temporal_model_version": self.TEMPORAL_MODEL_VERSION,
            "project_id": self.project_id,
            "project_type": "domain",
            "current_version": 0,
            "artifacts": {},
            "entities": {},
            "counts": {},
            "timelines": [],
            "temporal_model": {
                "version": self.TEMPORAL_MODEL_VERSION,
                "timeline_schema_version": self.TIMELINE_SCHEMA_VERSION,
                "axes": ["narrative", "story"],
                "narrative": "document/chat progression; independent from story chronology",
                "story": "in-story chronology; may diverge from narrative order",
            },
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
            "temporal_model_version": self.TEMPORAL_MODEL_VERSION,
            "project_id": self.project_id,
            "project_type": "domain",
            "current_version": 0,
            "artifacts": {},
            "entities": {},
            "counts": {},
            "timelines": [],
            "temporal_model": {
                "version": self.TEMPORAL_MODEL_VERSION,
                "timeline_schema_version": self.TIMELINE_SCHEMA_VERSION,
                "axes": ["narrative", "story"],
                "narrative": "document/chat progression; independent from story chronology",
                "story": "in-story chronology; may diverge from narrative order",
            },
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
                "temporal_position": None,
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

    @staticmethod
    def _normalize_provenance_item(item: dict):
        """Normalize provenance while retaining the most precise supplied locator.

        Precision is represented explicitly rather than inferred from a coarse
        section label.  The caller/source parser may supply any combination of
        page, section, line, word/character offsets, source chunk, node/segment,
        or a source-specific locator.  We retain all supplied locator metadata
        and record the finest-grained locator actually present.
        """
        if not isinstance(item, dict):
            return None
        out = dict(item)
        locator = out.get("locator")
        if isinstance(locator, dict):
            locator_data = dict(locator)
        elif locator is not None:
            locator_data = {"value": locator}
        else:
            locator_data = {}

        aliases = {
            "page": ("page", "page_number"),
            "section": ("section", "section_id", "section_title"),
            "line": ("line", "line_start", "line_number"),
            "word_offset": ("word_offset", "word_start", "word_index"),
            "char_offset": ("char_offset", "character_offset", "char_start"),
            "source_chunk": ("source_chunk", "chunk", "chunk_id", "chunk_index"),
            "node_id": ("node_id", "node_ids"),
            "segment_id": ("segment_id",),
        }
        for canonical, keys in aliases.items():
            if canonical in out and out[canonical] is not None:
                locator_data.setdefault(canonical, out[canonical])
            for key in keys:
                if key in out and out[key] is not None:
                    locator_data.setdefault(canonical, out[key])
                    break

        precision_order = (
            ("char_offset", 70),
            ("word_offset", 60),
            ("line", 50),
            ("source_chunk", 40),
            ("segment_id", 35),
            ("node_id", 30),
            ("section", 20),
            ("page", 10),
            ("value", 1),
        )
        best = next((name for name, _ in precision_order if locator_data.get(name) is not None), None)
        if locator_data:
            out["locator"] = locator_data
            if best:
                out["locator_precision"] = best
        return out

    def _record_provenance(self, data: dict, provenance):
        if not provenance:
            return
        entries = data.setdefault("provenance", [])
        items = provenance if isinstance(provenance, list) else [provenance]
        for item in items:
            normalized = self._normalize_provenance_item(item)
            if normalized is None:
                continue
            signature = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
            if not any(json.dumps(x, sort_keys=True, ensure_ascii=False) == signature for x in entries):
                entries.append(normalized)

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

    @staticmethod
    def _state_axis_keys(state: dict):
        """Return every independently comparable temporal coordinate on a state.

        A state may have both a narrative position and a story-world position.
        They are separate indexes over the same canonical state; neither axis
        is discarded merely because the other one is present.
        """
        temporal = state.get("temporal_position") or _normalize_temporal_position(
            state.get("timeline"), state.get("story_time"), state.get("timeline_sequence")
        )
        story = temporal.get("story", {})
        narrative = temporal.get("narrative", {})
        keys = []
        if story.get("ordinal") is not None:
            kind = str(story.get("kind") or "story")
            keys.append(("story", (kind, int(story["ordinal"]), int(story.get("scene") or 0))))
        elif story.get("chapter") is not None:
            keys.append(("story", ("chapter", int(story["chapter"]), int(story.get("scene") or 0))))
        if narrative.get("sequence") is not None:
            keys.append(("narrative", (int(narrative["sequence"]),)))
        elif narrative.get("chapter") is not None:
            keys.append(("narrative", (int(narrative["chapter"]), int(narrative.get("scene") or 0))))
        return keys

    @staticmethod
    def _state_axis_and_key(state: dict):
        """Compatibility helper returning the first canonical temporal axis."""
        keys = CognitionStore._state_axis_keys(state)
        return keys[0] if keys else (None, None)

    def _recompute_state_intervals(self, states: list[dict]):
        """Maintain independent half-open intervals for both temporal axes.

        One canonical state can carry both narrative and story coordinates.
        Their interval boundaries must therefore be stored separately; a later
        update on one axis must never overwrite the boundary computed for the
        other axis.
        """
        groups = {}
        for state in states:
            for axis, key in self._state_axis_keys(state):
                groups.setdefault(axis, []).append((key, state))

        for axis, items in groups.items():
            items.sort(key=lambda item: item[0])
            for idx, (_key, state) in enumerate(items):
                boundaries = state.setdefault("validity", {})
                axis_boundary = boundaries.setdefault(axis, {})
                explicit = bool(state.get("valid_to_explicit"))
                if explicit:
                    axis_boundary.setdefault("valid_to", state.get("valid_to"))
                    axis_boundary.setdefault("valid_to_temporal_position", state.get("valid_to_temporal_position"))
                    continue
                next_state = items[idx + 1][1] if idx + 1 < len(items) else None
                next_temporal = next_state.get("temporal_position") if next_state else None
                axis_boundary["valid_to"] = next_state.get("valid_from") if next_state else None
                axis_boundary["valid_to_temporal_position"] = next_temporal

        # Retain legacy top-level fields only as compatibility metadata. They no
        # longer participate in temporal resolution because they cannot represent
        # two independent axes without ambiguity.
        for state in states:
            validity = state.get("validity") or {}
            if "story" in validity and "narrative" not in validity:
                state["validity_axis"] = "story"
            elif "narrative" in validity and "story" not in validity:
                state["validity_axis"] = "narrative"
            else:
                state["validity_axis"] = "both" if validity else None

    @staticmethod
    def _temporal_request_axis(position: dict):
        """Return the requested temporal axis and comparable coordinate.

        Resolution never converts an unstructured story-time string into a
        guessed coordinate. A story request is resolvable only when it carries
        an explicit ordinal/chapter/scene coordinate; narrative resolution uses
        an explicit sequence/chapter/scene coordinate.
        """
        if not isinstance(position, dict):
            return None, None
        temporal = position.get("temporal_position") if isinstance(position.get("temporal_position"), dict) else position
        story = temporal.get("story") if isinstance(temporal.get("story"), dict) else None
        narrative = temporal.get("narrative") if isinstance(temporal.get("narrative"), dict) else None
        if story:
            if story.get("ordinal") is not None:
                return "story", (str(story.get("kind") or "story"), int(story["ordinal"]), int(story.get("scene") or 0))
            if story.get("chapter") is not None:
                return "story", ("chapter", int(story["chapter"]), int(story.get("scene") or 0))
        if narrative:
            if narrative.get("sequence") is not None:
                return "narrative", (int(narrative["sequence"]),)
            if narrative.get("chapter") is not None:
                return "narrative", (int(narrative["chapter"]), int(narrative.get("scene") or 0))
        return None, None

    @staticmethod
    def _state_temporal_position(state: dict):
        position = state.get("temporal_position")
        if isinstance(position, dict):
            return position
        return _normalize_temporal_position(
            state.get("timeline"),
            state.get("story_time"),
            state.get("timeline_sequence"),
        )

    def _resolve_states_at(self, states: list[dict], as_of: dict | None = None):
        """Resolve one historical state using the canonical temporal intervals.

        Intervals are half-open: [valid_from, valid_to). The resolver selects
        only states on the requested axis and never falls back from story time
        to narrative order. If the requested position cannot be compared, the
        result explicitly reports that instead of choosing a potentially wrong
        state.
        """
        states = list(states or [])
        if not states:
            return {"found": False, "reason": "no_states", "state": None}
        axis, target = self._temporal_request_axis(as_of or {})
        if axis is None or target is None:
            return {"found": False, "reason": "unresolvable_temporal_position", "state": None}

        matches = []
        for state in states:
            state_keys = dict(self._state_axis_keys(state))
            start = state_keys.get(axis)
            if start is None or start > target:
                continue
            end = None
            validity = state.get("validity") or {}
            axis_boundary = validity.get(axis) if isinstance(validity, dict) else None
            end_position = axis_boundary.get("valid_to_temporal_position") if isinstance(axis_boundary, dict) else None
            if isinstance(end_position, dict):
                end_axis, end = self._temporal_request_axis({"temporal_position": end_position})
                if end_axis != axis:
                    end = None
            if end is not None and target >= end:
                continue
            matches.append(state)

        if not matches:
            return {"found": False, "reason": "no_state_at_position", "axis": axis, "position": target, "state": None}
        matches.sort(key=lambda state: dict(self._state_axis_keys(state)).get(axis))
        state = matches[-1]
        return {
            "found": True,
            "axis": axis,
            "position": target,
            "state": state,
        }

    def resolve_attribute_state(self, entity_id, attribute, as_of):
        """Resolve an entity attribute at an explicit historical position."""
        entity = self._read_entity(str(entity_id))
        if not entity:
            return {"found": False, "reason": "unknown_entity", "entity_id": str(entity_id), "attribute": str(attribute)}
        states = (entity.get("attributes") or {}).get(str(attribute), [])
        result = self._resolve_states_at(states, as_of)
        result.update({"entity_id": str(entity_id), "attribute": str(attribute)})
        return result

    def resolve_entity_state(self, entity_id, as_of, attributes=None):
        """Resolve all requested entity attributes at one historical position."""
        entity = self._read_entity(str(entity_id))
        if not entity:
            return {"found": False, "reason": "unknown_entity", "entity_id": str(entity_id), "attributes": {}}
        names = [str(x) for x in attributes] if attributes else list((entity.get("attributes") or {}).keys())
        resolved = {}
        for name in names:
            item = self._resolve_states_at((entity.get("attributes") or {}).get(name, []), as_of)
            if item.get("found"):
                resolved[name] = item["state"]
        return {
            "found": bool(resolved),
            "entity_id": str(entity_id),
            "position": as_of,
            "attributes": resolved,
        }

    def _append_attribute(self, data: dict, attr: str, value, timeline=None, source_artifact=None, locator=None, summary="", sequence=None, valid_from=None, valid_to=None, event_id=None, temporal_position=None):
        states = data.setdefault("attributes", {}).setdefault(str(attr), [])
        temporal = temporal_position or _normalize_temporal_position(timeline, None, sequence)
        sig = json.dumps({"value": value, "temporal_position": temporal, "source_artifact": source_artifact, "locator": locator, "event_id": event_id}, sort_keys=True, ensure_ascii=False)
        if any(json.dumps({"value": s.get("value"), "temporal_position": s.get("temporal_position") or _normalize_temporal_position(s.get("timeline"), s.get("story_time"), s.get("timeline_sequence")), "source_artifact": s.get("source_artifact"), "locator": s.get("source_locator"), "event_id": s.get("event_id")}, sort_keys=True, ensure_ascii=False) == sig for s in states):
            return
        narrative = temporal.get("narrative", {})
        explicit_valid_to = valid_to is not None
        rec = {
            "value": value,
            "summary": summary,
            "timeline": timeline,
            "temporal_position": temporal,
            "timeline_sequence": narrative.get("sequence", sequence if sequence is not None else _timeline_rank(timeline)),
            "valid_from": valid_from or timeline,
            "valid_to": valid_to,
            "valid_from_temporal_position": temporal,
            "valid_to_temporal_position": None,
            "validity_axis": None,
            "valid_to_explicit": explicit_valid_to,
            "event_id": event_id,
            "source_artifact": source_artifact,
            "source_locator": locator,
            "recorded_at": now_iso(),
            "sequence": len(states) + 1,
        }
        states.append(rec)
        self._recompute_state_intervals(states)

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
        temporal_position = _normalize_temporal_position(
            update.get("narrative_position") or {"label": timeline, "sequence": update.get("sequence", default_sequence)},
            update.get("story_time"),
            update.get("sequence", default_sequence),
        )
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
                    temporal_position=value.get("temporal_position") or _normalize_temporal_position(value.get("narrative_position") or {"label": value.get("timeline", timeline), "sequence": value.get("sequence", default_sequence)}, value.get("story_time"), value.get("sequence", default_sequence)),
                )
            else:
                self._append_attribute(data, attr, value, timeline, source_artifact, None, "", default_sequence, temporal_position=temporal_position)
        for ref_kind in ("relationships", "events", "locations", "concepts", "definitions", "knowledge_links"):
            refs = []
            for item in update.get(ref_kind) or []:
                if isinstance(item, str):
                    refs.append({"id": item})
                elif isinstance(item, dict) and item.get("id"):
                    refs.append({"id": str(item["id"])})
            data[ref_kind] = self._append_unique(data.get(ref_kind, []), refs)
        data["last_update_temporal_position"] = temporal_position
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
            timeline_entry = {
                "sequence": default_sequence,
                "timeline": timeline,
                "narrative_position": temporal_position.get("narrative", {}),
                "story_time": temporal_position.get("story", {}),
                "temporal_position": temporal_position,
                "kind": "entity_update",
                "object_id": eid,
            }
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
        temporal_position = _normalize_temporal_position({"label": timeline}, record.get("story_time"), record.get("sequence"))
        if record.get("temporal_position"):
            temporal_position = record["temporal_position"]
        rec["last_update_temporal_position"] = temporal_position
        if addition:
            observation = {
                "sequence": len(rec["evolution"]) + 1,
                "timeline": timeline,
                "temporal_position": temporal_position,
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
            self.append_timeline_entry(timeline or "document", {"sequence": len(rec["evolution"]), "temporal_position": temporal_position, "kind": kind, "object_id": ident})
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
        temporal_position = _normalize_temporal_position({"label": timeline, "sequence": sequence}, None, sequence)
        self._append_attribute(data, attribute, new_value, timeline=timeline, source_artifact=source_artifact, locator=locator, summary=description, sequence=sequence, valid_from=timeline, event_id=event_id, temporal_position=temporal_position)
        entry = {
            "sequence": sequence,
            "timeline": timeline,
            "narrative_position": temporal_position.get("narrative", {}),
            "story_time": temporal_position.get("story", {}),
            "temporal_position": temporal_position,
            "kind": "state_change",
            "object_id": event_id,
            "entity_ids": [entity_id],
            "attribute": attribute,
            "caused_by_event_id": event_id,
        }
        data["timeline"] = self._append_unique(data.get("timeline", []), [entry])
        self._record_provenance(data, [{"artifact_id": source_artifact, "locator": locator, "event_id": event_id}])
        data["updated_at"] = now_iso()
        self._write_json(path, data)
        self.db.sync_entity_index(self, entity_id)
        self.append_timeline_entry(timeline, entry)
        return True

    def add_relationship(self, rel, default_source=None, source_artifact=None, timeline=None, data=None):
        """Persist one canonical relationship with an explicit participant set.

        ``participants`` is authoritative.  Each participant is a structured
        reference with an id, kind, and optional role.  ``source``/``target``
        remain as derived compatibility fields for the existing binary index
        and callers; they are never the canonical representation.
        """
        if not isinstance(rel, dict):
            return None

        raw_participants = rel.get("participants")
        participants = []
        if isinstance(raw_participants, (list, tuple)):
            for item in raw_participants:
                if isinstance(item, str):
                    pid = item.strip()
                    if pid:
                        participants.append({"id": pid, "kind": "entity"})
                elif isinstance(item, dict):
                    pid = item.get("id") or item.get("entity_id") or item.get("participant_id")
                    if pid:
                        participant = {
                            "id": str(pid),
                            "kind": str(item.get("kind") or item.get("type") or "entity"),
                        }
                        if item.get("role") is not None:
                            participant["role"] = str(item["role"])
                        participants.append(participant)

        source = str(rel.get("source") or rel.get("from") or default_source or "")
        target = str(rel.get("target") or rel.get("to") or "")
        if not participants:
            if source:
                participants.append({"id": source, "kind": str(rel.get("source_kind") or "entity")})
            if target and target != source:
                participants.append({"id": target, "kind": str(rel.get("target_kind") or "entity")})
        if not participants or len(participants) < 2:
            return None

        # Preserve participant order because roles/direction can be meaningful.
        deduped = []
        seen = set()
        for participant in participants:
            key = (participant.get("kind", "entity"), participant.get("id"), participant.get("role"))
            if key not in seen:
                deduped.append(participant)
                seen.add(key)
        participants = deduped
        if len(participants) < 2:
            return None

        if not source:
            source = participants[0]["id"]
        if not target:
            target = participants[1]["id"]

        relation_type = rel.get("type") or rel.get("relation_type") or "related_to"
        participant_signature = "|".join(
            f"{p.get('kind', 'entity')}:{p.get('id')}:{p.get('role', '')}" for p in participants
        )
        rid = str(rel.get("id") or f"relationship:{relation_type}:{participant_signature}")
        existing = self._read_object("relationship", rid)
        temporal_position = rel.get("temporal_position") or _normalize_temporal_position(
            {"label": rel.get("timeline", timeline), "sequence": rel.get("sequence")},
            rel.get("story_time"),
            rel.get("sequence"),
        )
        rec = dict(existing)
        rec.update({
            "schema_version": self.SCHEMA_VERSION,
            "id": rid,
            "type": relation_type,
            "participants": participants,
            # Compatibility projections.  Consumers should prefer participants.
            "source": source,
            "target": target,
            "source_kind": participants[0].get("kind", "entity"),
            "target_kind": participants[1].get("kind", "entity"),
            "source_ref": {"kind": participants[0].get("kind", "entity"), "id": source},
            "target_ref": {"kind": participants[1].get("kind", "entity"), "id": target},
            "state": rel.get("state", existing.get("state")),
            "description": self._append_text(existing.get("description", ""), rel.get("description") or rel.get("state") or ""),
            "timeline": rel.get("timeline", timeline),
            "last_update_temporal_position": temporal_position,
            "evolution": list(existing.get("evolution") or []),
            "provenance": list(existing.get("provenance") or []),
            "created_at": existing.get("created_at", now_iso()),
            "updated_at": now_iso(),
        })
        observation = {
            "sequence": len(rec["evolution"]) + 1,
            "timeline": rel.get("timeline", timeline),
            "temporal_position": temporal_position,
            "participants": participants,
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

        # SQLite remains a derived binary-relation index.  The canonical JSON
        # retains the complete n-ary participant set.
        self.db.add_relation({
            "id": rid,
            "from_id": source,
            "to_id": target,
            "relation_type": rec["type"],
            "description": rec["description"],
            "timeline": rec.get("timeline"),
            "source_artifact": source_artifact,
        })

        for participant in participants:
            self._link_object_reference(
                participant.get("kind", "entity"),
                participant["id"],
                "relationships",
                rid,
                as_object=True,
            )
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
        temporal_position = event.get("temporal_position") or _normalize_temporal_position(pos, event.get("story_time", pos.get("story_time")), pos.get("sequence", sequence))
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
            "temporal_position": temporal_position,
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
            "temporal_position": temporal_position,
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
                        "temporal_position": temporal_position,
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
            "narrative_position": temporal_position.get("narrative", {}),
            "story_time": temporal_position.get("story", {}),
            "temporal_position": temporal_position,
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
        """Append one canonical temporal transition/reference to the global timeline.

        The timeline is the authoritative temporal index. It records where a
        cognition change occurred in narrative progression and, independently,
        when the represented state/event exists in story time. It references
        canonical cognition objects instead of duplicating their prose.
        """
        timeline_id = str(timeline_id or "document")
        global_path = self.timeline_root / "timeline.json"
        existing = self._read_json(
            global_path,
            {
                "schema_version": self.SCHEMA_VERSION,
                "timeline_schema_version": self.TIMELINE_SCHEMA_VERSION,
                "temporal_model_version": self.TEMPORAL_MODEL_VERSION,
                "id": "timeline",
                "entries": [],
            },
        )
        existing["schema_version"] = self.SCHEMA_VERSION
        existing["timeline_schema_version"] = self.TIMELINE_SCHEMA_VERSION
        existing["temporal_model_version"] = self.TEMPORAL_MODEL_VERSION

        entry = dict(entry or {})
        entry.setdefault("timeline", timeline_id)

        temporal = entry.get("temporal_position")
        if not isinstance(temporal, dict):
            temporal = _normalize_temporal_position(
                narrative=entry.get("narrative_position") or {
                    "label": entry.get("timeline"),
                    "sequence": entry.get("sequence"),
                },
                story_time=entry.get("story_time"),
                sequence=entry.get("sequence"),
            )
        else:
            # Re-normalize supplied positions so every timeline entry has the
            # same canonical shape and model version.
            temporal = _normalize_temporal_position(
                narrative=temporal.get("narrative"),
                story_time=temporal.get("story"),
                sequence=entry.get("sequence"),
            )

        entry["temporal_position"] = temporal
        entry["narrative_position"] = temporal["narrative"]
        entry["story_time"] = temporal["story"]
        entry["timeline_schema_version"] = self.TIMELINE_SCHEMA_VERSION

        entries = existing.setdefault("entries", [])

        def identity(item):
            return json.dumps({
                "kind": item.get("kind"),
                "object_id": item.get("object_id"),
                "attribute": item.get("attribute"),
                "entity_ids": item.get("entity_ids") or [],
                "narrative_position": item.get("narrative_position") or {},
                "story_time": item.get("story_time") or {},
            }, sort_keys=True, ensure_ascii=False)

        incoming_id = identity(entry)
        replaced = False
        for index, old in enumerate(entries):
            if identity(old) != incoming_id:
                continue
            # Preserve the canonical entry while enriching it with fields that
            # became available later. Never create a second timeline record for
            # the same semantic transition.
            merged = dict(old)
            for key, value in entry.items():
                if value is not None and (key not in merged or merged[key] in (None, "", [], {})):
                    merged[key] = value
            entries[index] = merged
            replaced = True
            break
        if not replaced:
            entries.append(entry)

        def sort_key(item):
            n = item.get("narrative_position") or {}
            return (
                n.get("sequence") is None,
                n.get("sequence") if n.get("sequence") is not None else 10**12,
                n.get("chapter") if n.get("chapter") is not None else 10**12,
                n.get("scene") if n.get("scene") is not None else 10**12,
                str(n.get("session") or ""),
                n.get("turn") if n.get("turn") is not None else 10**12,
                str((item.get("story_time") or {}).get("label") or ""),
                str(item.get("object_id") or ""),
            )

        entries.sort(key=sort_key)
        existing["updated_at"] = now_iso()
        self._write_json(global_path, existing)

        m = self.master_metadata()
        timelines = set(m.get("timelines") or [])
        timelines.add(timeline_id)
        m["timelines"] = sorted(timelines)
        m["timeline_schema_version"] = self.TIMELINE_SCHEMA_VERSION
        m["temporal_model_version"] = self.TEMPORAL_MODEL_VERSION
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

    def commit_authoritative_change(self, message):
        """Persist the current canonical state as one Git revision.

        Git is versioning/provenance infrastructure only; canonical cognition
        remains filesystem-authoritative.
        """
        from ragapp.core.vcs import VCSManager
        return VCSManager(self).commit(str(message or "Canonical cognition update"))

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
