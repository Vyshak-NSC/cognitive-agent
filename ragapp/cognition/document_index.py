"""Compact, source-location-only document index.

The index is deliberately metadata-only: it never stores document body text.
It maps cognition to the exact structural location needed to fetch source data
later without scanning the original artifact.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class DocumentIndex:
    SCHEMA_VERSION = 1

    def __init__(self, store):
        self.store = store
        self.path = store.index_root / "document_index.json"
        self._ensure()

    def _ensure(self):
        if not self.path.exists():
            self._write({"schema_version": self.SCHEMA_VERSION, "documents": {}, "nodes": {}, "segments": {}, "entity_locations": {}, "event_locations": {}, "relationship_locations": {}})

    def _read(self):
        self._ensure()
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"schema_version": self.SCHEMA_VERSION, "documents": {}, "nodes": {}, "segments": {}, "entity_locations": {}, "event_locations": {}, "relationship_locations": {}}

    def _write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _public_node(node):
        # Children can contain thousands of paragraph IDs for a large chapter.
        # Persist a bounded preview plus a count; callers can resolve a specific
        # child/section by ID instead of receiving a giant metadata payload.
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
            "attributes": {k: v for k, v in (node.attributes or {}).items() if k not in {"runs"}},
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
        for bucket in ("entity_locations", "event_locations", "relationship_locations"):
            for ident, locs in list(data.get(bucket, {}).items()):
                kept = [x for x in locs if x.get("artifact_id") != artifact_id]
                if kept:
                    data[bucket][ident] = kept
                else:
                    data[bucket].pop(ident, None)
        if write:
            self._write(data)

    def attach(self, kind, ident, artifact_id, node_ids=None, segment_id=None, locator=None, section_ids=None, chapter_id=None, scene_id=None):
        if kind not in {"entity", "event", "relationship"}:
            raise ValueError(kind)
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
        node = self.node(section_id)
        if node:
            return node
        return self.segment(section_id)

    def sections(self, artifact_id=None, query=None, limit=200):
        data = self._read().get("nodes", {})
        out=[]
        q=(str(query).lower() if query else None)
        for node in data.values():
            if node.get("type") not in {"chapter","scene","section","subsection","heading"}:
                continue
            if artifact_id and node.get("artifact_id") != artifact_id:
                continue
            if q and q not in str(node.get("title") or "").lower() and q not in str(node.get("id") or "").lower():
                continue
            out.append(node)
        out.sort(key=lambda x: (x.get("artifact_id", ""), x.get("order", 0), x.get("id", "")))
        return out[:limit]

    def locations(self, kind, ident):
        return list(self._read().get(f"{kind}_locations", {}).get(str(ident), []))

    def resolve_section(self, query, artifact_id=None):
        candidates=self.sections(artifact_id=artifact_id, query=query, limit=100)
        if not candidates:
            return None
        # Exact title/number first.
        q=str(query).strip().lower()
        for c in candidates:
            if str(c.get("title") or "").strip().lower() == q:
                return c
        return candidates[0]

    def retrieval_index(self, limit=1000):
        data=self._read()
        docs=list(data.get("documents", {}).values())
        sections=self.sections(limit=limit)
        return {
            "documents": docs,
            "sections": sections,
            "counts": {
                "documents": len(docs),
                "nodes": len(data.get("nodes", {})),
                "segments": len(data.get("segments", {})),
                "entity_locations": sum(len(v) for v in data.get("entity_locations", {}).values()),
                "event_locations": sum(len(v) for v in data.get("event_locations", {}).values()),
            },
        }
