from __future__ import annotations

from ragapp.core.metadata import MetadataDB


def sync_store(store):
    """Synchronize the deterministic SQLite index from the authoritative store."""
    db = MetadataDB(store)
    db.rebuild_entity_index(store)

    master = store.master_metadata()
    for aid, artifact in master.get("artifacts", {}).items():
        db.upsert_artifact(artifact)

    return db
