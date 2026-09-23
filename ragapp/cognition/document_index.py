"""Structure/provenance index for source documents.

This is an index, not cognition. It stores document structure and exact source
locators so canonical cognition objects can point back to evidence without
copying source text into cognition.
"""
from __future__ import annotations

import json


class DocumentIndex:
    SCHEMA_VERSION = 2
    LOCATION_KINDS = ("entity", "event", "relationship", "location", "concept", "definition", "knowledge")

    def __init__(self, store):
        self.store = store
        self.path = store.index_root / "document_index.json"
        self._ensure()

    @staticmethod
    def _empty():
        return {
            "schema_version": DocumentIndex.SCHEMA_VERSION,
            "documents": {},
            "nodes": {},
            "segments": {},
            "entity_locations": {},
            "event_locations": {},
            "relationship_locations": {},
            "location_locations": {},
            "concept_locations": {},
            "definition_locations": {},
            "knowledge_locations": {},
        }

    def _ensure(self):
        if not self.path.exists():
            self._write(self._empty())

    def _read(self):
        self._ensure()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            base = self._empty()
            base.update(data)
            return base
        except Exception:
            return self._empty()

    def _write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _public_node(node):
        children = list(node.children or [])
        return {
            "id": node.id,
            "type": node.type,
            "order": node.order,
            "title": node.title,
            "parent_id": node.parent_id,
            "children": children[:20],
            "children_count": len(children),
            "children_truncated": len(children) > 20,
            "locator": dict(node.locator or {}),
            "attributes": {k: v for k, v in (node.attributes or {}).items() if k != "runs"},
        }

    def replace_document(self, parsed):
        data = self._read()
        self.remove_artifact(parsed.artifact_id, data=data, write=False)
        data["documents"][parsed.artifact_id] = {
            "artifact_id": parsed.artifact_id,
            "area": parsed.area,
            "path": parsed.path,
            "format": parsed.format,
            "title": parsed.title,
            "root_id": parsed.root_id,
            "metadata": dict(parsed.metadata),
            "node_count": parsed.node_count,
            "segment_count": len(parsed.segments),
        }
        for node in parsed.nodes.values():
            public = self._public_node(node)
            public["artifact_id"] = parsed.artifact_id
            public["path"] = parsed.path
            data["nodes"][node.id] = public
        for seg in parsed.segments:
            data["segments"][seg.id] = {
                "id": seg.id,
                "artifact_id": parsed.artifact_id,
                "path": parsed.path,
                "title": seg.title,
                "node_ids": list(seg.node_ids),
                "section_ids": list(seg.section_ids),
                "locator": dict(seg.locator),
                "chapter_id": seg.chapter_id,
                "scene_id": seg.scene_id,
            }
        self._write(data)
        return data["documents"][parsed.artifact_id]

    def remove_artifact(self, artifact_id, data=None, write=True):
        data = data if data is not None else self._read()
        data.get("documents", {}).pop(artifact_id, None)
        for bucket in ("nodes", "segments"):
            for ident in list(data.get(bucket, {})):
                if data[bucket][ident].get("artifact_id") == artifact_id:
                    data[bucket].pop(ident, None)
        for kind in self.LOCATION_KINDS:
            bucket = data.get(f"{kind}_locations", {})
            for ident, locs in list(bucket.items()):
                kept = [x for x in locs if x.get("artifact_id") != artifact_id]
                if kept:
                    bucket[ident] = kept
                else:
                    bucket.pop(ident, None)
        if write:
            self._write(data)

    def attach(self, kind, ident, artifact_id, node_ids=None, segment_id=None, locator=None, section_ids=None, chapter_id=None, scene_id=None):
        if kind not in self.LOCATION_KINDS:
            raise ValueError(kind)
        if not artifact_id:
            return
        data = self._read()
        bucket = data[f"{kind}_locations"]
        item = {
            "artifact_id": artifact_id,
            "node_ids": list(node_ids or []),
            "segment_id": segment_id,
            "locator": dict(locator or {}),
            "section_ids": list(section_ids or []),
            "chapter_id": chapter_id,
            "scene_id": scene_id,
        }
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        existing = bucket.setdefault(str(ident), [])
        if not any(json.dumps(x, sort_keys=True, ensure_ascii=False) == key for x in existing):
            existing.append(item)
            self._write(data)

    def document(self, artifact_id):
        return self._read().get("documents", {}).get(artifact_id)

    def node(self, node_id):
        return self._read().get("nodes", {}).get(node_id)

    def segment(self, segment_id):
        return self._read().get("segments", {}).get(segment_id)

    def section(self, section_id):
        return self.node(section_id) or self.segment(section_id)

    def sections(self, artifact_id=None, query=None, limit=200):
        nodes = self._read().get("nodes", {})
        q = str(query).strip().casefold() if query else None
        out = []
        for node in nodes.values():
            if node.get("type") not in {"chapter", "scene", "section", "subsection", "heading"}:
                continue
            if artifact_id and node.get("artifact_id") != artifact_id:
                continue
            title = str(node.get("title") or "")
            if q and q not in title.casefold() and q not in str(node.get("id") or "").casefold():
                continue
            out.append(node)
        out.sort(key=lambda x: (x.get("artifact_id", ""), x.get("order", 0), x.get("id", "")))
        return out[:limit]

    def locations(self, kind, ident):
        if kind not in self.LOCATION_KINDS:
            return []
        return list(self._read().get(f"{kind}_locations", {}).get(str(ident), []))

    def resolve_section(self, query, artifact_id=None):
        candidates = self.sections(artifact_id=artifact_id, query=query, limit=100)
        if not candidates:
            return None
        q = str(query).strip().casefold()
        for c in candidates:
            if str(c.get("title") or "").strip().casefold() == q:
                return c
        return candidates[0]

    def retrieval_index(self, limit=1000):
        data = self._read()
        sections = self.sections(limit=limit)
        return {
            "documents": list(data.get("documents", {}).values()),
            "sections": sections,
            "counts": {
                "documents": len(data.get("documents", {})),
                "nodes": len(data.get("nodes", {})),
                "segments": len(data.get("segments", {})),
                **{f"{kind}_locations": sum(len(v) for v in data.get(f"{kind}_locations", {}).values()) for kind in self.LOCATION_KINDS},
            },
        }
