from __future__ import annotations

from ragapp.cognition.compiler import compile_project


class ReingestPipeline:
    """Selectively rebuild cognition derived from authoritative source files."""

    def __init__(self, store):
        self.store = store

    def after_approval(self, target, metadata=None):
        metadata = metadata or {}
        affected = {self._source_pair(target)}

        # Explicit source-relative files supplied by the agent.
        for path in metadata.get("affected_files", []) or []:
            if isinstance(path, str):
                affected.add(self._source_pair(path))

        # Expand from the persistent cognition graph. This is the key agentic
        # behavior: the graph, not the current batch, tells us what depends on
        # the changed file/entity.
        target_entity = metadata.get("target_entity_id")
        if target_entity:
            for entity_id in self._affected_entity_ids(str(target_entity)):
                affected.update(
                    ("source", path)
                    for path in self._entity_source_paths(entity_id)
                )

        # Also resolve the changed source file's own persisted file entity.
        target_artifact = f"source:{self._source_pair(target)[1]}"
        for entity_id in self._entities_for_artifact(target_artifact):
            for related in self._affected_entity_ids(entity_id):
                affected.update(
                    ("source", path)
                    for path in self._entity_source_paths(related)
                )

        affected = {
            pair for pair in affected
            if pair[0] == "source" and self._valid_relative_path(pair[1])
        }

        existing = set(self._source_files())
        selected = sorted(pair for pair in affected if pair in existing)

        if not selected:
            return {
                "status": "no_affected_source_files",
                "project_id": self.store.project_id,
                "source_files": 0,
                "chunks": 0,
                "entities": 0,
                "relationships": 0,
            }

        old_artifacts = {f"source:{rel}" for _, rel in selected}
        self.store.remove_source_projection(old_artifacts)

        return compile_project(
            self.store,
            selected_files=selected,
            reconcile_selected=False,
        )

    def full(self):
        source_files = self._source_files()
        self.store.reset_source_derived_cognition()
        return compile_project(
            self.store,
            selected_files=source_files,
            reconcile_selected=False,
        )

    def affected(self, entity_ids):
        result = set()
        for entity_id in entity_ids or []:
            result.add(str(entity_id))
            result.update(self._affected_entity_ids(str(entity_id)))
        return {"status": "scheduled", "entities": sorted(result)}

    def _source_pair(self, path):
        path = str(path).replace("\\", "/").strip()
        while path.startswith("./"):
            path = path[2:]
        if path.startswith("source/"):
            path = path[len("source/"):]
        if path.startswith(("workspace/", "cognition/")):
            raise ValueError(f"Invalid source path: {path!r}")
        return ("source", path)

    @staticmethod
    def _valid_relative_path(path):
        if not path or path.startswith("/"):
            return False
        parts = path.split("/")
        return not any(x in {"", ".", ".."} for x in parts) and parts[0] not in {"source", "workspace", "cognition"}

    def _source_files(self):
        if not self.store.source.exists():
            return []
        return [
            ("source", p.relative_to(self.store.source).as_posix())
            for p in sorted(self.store.source.rglob("*"))
            if p.is_file()
        ]

    def _entities_for_artifact(self, artifact_id):
        out = []
        master = self.store.master_metadata()
        for eid, meta in master.get("entities", {}).items():
            path = meta.get("path")
            if not path:
                continue
            entity = self.store._read_json(self.store.root / path, {})
            if artifact_id in (entity.get("source_projections") or {}):
                out.append(eid)
            elif artifact_id in (entity.get("sources") or []):
                out.append(eid)
        return out

    def _affected_entity_ids(self, entity_id):
        affected = set()
        queue = [entity_id]
        while queue:
            current = queue.pop(0)
            if current in affected:
                continue
            affected.add(current)
            for relation in self.store.db.relations_for([current], limit=1000):
                source = relation.get("from_id")
                target = relation.get("to_id")
                if source == current and target and target not in affected:
                    queue.append(target)
                elif target == current and source and source not in affected:
                    queue.append(source)
        return affected

    def _entity_source_paths(self, entity_id):
        master = self.store.master_metadata()
        entity_meta = master.get("entities", {}).get(entity_id)
        if not entity_meta or not entity_meta.get("path"):
            return []
        entity = self.store._read_json(self.store.root / entity_meta["path"], {})
        paths = []
        for artifact_id in entity.get("sources", []) or []:
            artifact = master.get("artifacts", {}).get(artifact_id)
            if artifact and artifact.get("area") == "source" and artifact.get("path"):
                paths.append(artifact["path"])
        for artifact_id in (entity.get("source_projections") or {}):
            artifact = master.get("artifacts", {}).get(artifact_id)
            if artifact and artifact.get("area") == "source" and artifact.get("path"):
                paths.append(artifact["path"])
        return sorted(set(paths))
