from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

def now():
    return datetime.now(timezone.utc).isoformat()

class DraftManager:
    """Persist review metadata outside the user-visible workspace.

    ``.system/drafts`` is internal application state. Legacy drafts created by
    older versions under ``workspace/drafts`` are migrated automatically.
    """
    def __init__(self, store):
        self.store = store
        self.system_root = store.root / ".system"
        self.root = self.system_root / "drafts"
        self.legacy_root = store.workspace / "drafts"
        self.root.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy()

    def _migrate_legacy(self):
        if not self.legacy_root.exists():
            return
        for path in self.legacy_root.glob("*.json"):
            target = self.root / path.name
            if not target.exists():
                try:
                    shutil.move(str(path), str(target))
                except OSError:
                    pass
        try:
            self.legacy_root.rmdir()
        except OSError:
            pass

    def create(self, content, metadata=None, session_id=None):
        did = str(uuid.uuid4())
        path = self.root / f"{did}.json"
        data = {
            "id": did,
            "status": "pending",
            "created_at": now(),
            "session_id": session_id,
            "content": content,
            "metadata": metadata or {},
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return data

    def list(self, status=None):
        out = []
        for p in self.root.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if status is None or d.get("status") == status:
                    out.append(d)
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(out, key=lambda x: x.get("created_at", ""), reverse=True)

    def load(self, did):
        p = self.root / f"{did}.json"
        return json.loads(p.read_text(encoding="utf-8"))

    def save(self, d):
        p = self.root / f"{d['id']}.json"
        p.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        return d

    def discard(self, did, archive=False):
        d = self.load(did)
        d["status"] = "archived" if archive else "rejected"
        return self.save(d)
