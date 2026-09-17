"""Persistent chat-session storage scoped to a project."""
from __future__ import annotations
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
    return value.strip("._") or "chat"


class ChatSessionStore:
    def __init__(self, store):
        self.store = store
        self.root = store.sessions
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id):
        sid = _safe_name(session_id)
        p = (self.root / f"{sid}.json").resolve()
        p.relative_to(self.root.resolve())
        return p

    def list_sessions(self):
        items = []
        for p in self.root.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                items.append(data)
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(items, key=lambda x: x.get("updated_at", ""), reverse=True)

    def create(self, title="New chat"):
        sid = uuid.uuid4().hex
        now = now_iso()
        data = {"id": sid, "title": title, "created_at": now, "updated_at": now, "messages": []}
        self._path(sid).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return data

    def load(self, session_id):
        p = self._path(session_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def save(self, session):
        session["updated_at"] = now_iso()
        path = self._path(session["id"])
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)  # atomic: a crash mid-write leaves the old file intact, never a half-written one
        return session

    def append_turn(self, session_id, user_message, assistant_message, title_from=None):
        """Persist one full turn (user + assistant) as a single atomic write.
        Never call this with only a user_message and no reply -- if generation
        failed, assistant_message should be the error text, not omitted. This
        is what guarantees a user message is never saved on disk without a
        paired reply, which is what corrupts the next turn."""
        session = self.load(session_id)
        if session is None:
            session = {"id": session_id, "title": "New chat", "created_at": now_iso(), "messages": []}
        session["messages"].append(user_message)
        session["messages"].append(assistant_message)
        if title_from and session.get("title") == "New chat":
            session["title"] = title_from[:60].strip() or "New chat"
        return self.save(session)

    def delete(self, session_id):
        p = self._path(session_id)
        if p.exists():
            p.unlink()

    def rename(self, session_id, title):
        session = self.load(session_id)
        if not session:
            raise FileNotFoundError(session_id)
        session["title"] = title.strip() or "New chat"
        return self.save(session)