from __future__ import annotations

from ragapp.cognition.compiler import compile_project


class ReingestPipeline:
    """Selectively rebuild cognition using canonical-object provenance."""

    def __init__(self, store):
        self.store = store

    def after_approval(self, target, metadata=None):
        metadata = metadata or {}
        affected = {self._source_pair(target)}
        for path in metadata.get("affected_files", []) or []:
            if isinstance(path, str):
                affected.add(self._source_pair(path))

        target_entity = metadata.get("target_entity_id")
        if target_entity:
            for entity_id in self._affected_entity_ids(str(target_entity)):
                affected.update(("source", path) for path in self._entity_source_paths(entity_id))

        target_artifact = f"source:{self._source_pair(target)[1]}"
        for entity_id in self._entities_for_artifact(target_artifact):
            for related in self._affected_entity_ids(entity_id):
                affected.update(("source", path) for path in self._entity_source_paths(related))

        existing = set(self._source_files())
        selected = sorted(pair for pair in affected if pair in existing and self._valid_relative_path(pair[1]))
        if not selected:
            return {"status": "no_affected_source_files", "project_id": self.store.project_id, "source_files": 0, "chunks": 0, "entities": 0, "relationships": 0}

        self.store.remove_source_projection({f"source:{rel}" for _, rel in selected})
        return compile_project(self.store, selected_files=selected, reconcile_selected=False)

    def full(self):
        source_files = self._source_files()
        self.store.reset_source_derived_cognition()
        return compile_project(self.store, selected_files=source_files, reconcile_selected=False)

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
        return [("source", p.relative_to(self.store.source).as_posix()) for p in sorted(self.store.source.rglob("*")) if p.is_file()]

    def _entities_for_artifact(self, artifact_id):
        out = []
        for eid in self.store.master_metadata().get("entities", {}):
            entity = self.store.entity(eid)
            if any(isinstance(p, dict) and p.get("artifact_id") == artifact_id for p in entity.get("provenance", [])):
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
        entity = self.store.entity(entity_id)
        paths = []
        for prov in entity.get("provenance", []):
            aid = prov.get("artifact_id") if isinstance(prov, dict) else None
            artifact = master.get("artifacts", {}).get(aid)
            if artifact and artifact.get("area") == "source" and artifact.get("path"):
                paths.append(artifact["path"])
        return sorted(set(paths))
