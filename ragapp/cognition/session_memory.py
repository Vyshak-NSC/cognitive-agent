"""Incremental local session memory with conversation-branch lineage."""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib,json,uuid

def now(): return datetime.now(timezone.utc).isoformat()
class SessionMemory:
    def __init__(self,store):
        self.store=store; self.root=store.cognition/"sessions"; self.root.mkdir(parents=True,exist_ok=True); self.path=self.root/"episodes.jsonl"; self.path.touch(exist_ok=True)
    def _hash(self,session_id,role,content,turn_id=None): return hashlib.sha256(f"{session_id}\0{turn_id or ''}\0{role}\0{content}".encode()).hexdigest()
    def append(self,session_id,kind,content,provenance=None):
        rec={"id":uuid.uuid4().hex,"timestamp":now(),"session_id":session_id,"kind":kind,"content":content,"provenance":provenance or {}}
        with self.path.open("a",encoding="utf-8") as f:f.write(json.dumps(rec,ensure_ascii=False)+"\n")
        return rec
    def list(self,session_id=None,limit=200,active_turn_ids=None):
        allowed=set(active_turn_ids or []) if active_turn_ids is not None else None; out=[]
        for line in self.path.read_text(encoding="utf-8").splitlines()[-limit*4:]:
            try:
                x=json.loads(line)
                if session_id is not None and x.get("session_id")!=session_id: continue
                tid=(x.get("provenance") or {}).get("turn_id")
                if allowed is not None and tid and tid not in allowed: continue
                out.append(x)
            except Exception: pass
        return out[-limit:]
    def distill(self,session_id,transcript,turn_id=None,active_turn_ids=None):
        existing={x.get("provenance",{}).get("message_hash") for x in self.list(session_id,limit=5000)}; added=[]
        for m in transcript or []:
            role=m.get("role","user"); content=str(m.get("content","") or ""); mtid=m.get("turn_id") or turn_id
            if not content: continue
            h=self._hash(session_id,role,content,mtid)
            if h in existing: continue
            added.append(self.append(session_id,"message",content,{"role":role,"message_hash":h,"turn_id":mtid})); existing.add(h)
        return added
