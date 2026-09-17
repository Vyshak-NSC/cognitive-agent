"""Incremental local session memory. It never calls an LLM by itself."""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib,json,uuid

def now():return datetime.now(timezone.utc).isoformat()
class SessionMemory:
    def __init__(self,store):
        self.store=store; self.root=store.cognition/"sessions"; self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/"episodes.jsonl"; self.path.touch(exist_ok=True)
    def _hash(self,session_id,role,content):return hashlib.sha256(f"{session_id}\0{role}\0{content}".encode()).hexdigest()
    def append(self,session_id,kind,content,provenance=None):
        rec={"id":uuid.uuid4().hex,"timestamp":now(),"session_id":session_id,"kind":kind,"content":content,"provenance":provenance or {}}
        with self.path.open("a",encoding="utf-8") as f:f.write(json.dumps(rec,ensure_ascii=False)+"\n")
        return rec
    def list(self,session_id=None,limit=200):
        out=[]
        for line in self.path.read_text(encoding="utf-8").splitlines()[-limit*2:]:
            try:
                x=json.loads(line)
                if session_id is None or x.get("session_id")==session_id:out.append(x)
            except Exception:pass
        return out[-limit:]
    def distill(self,session_id,transcript):
        """Append only messages not already persisted for this session."""
        existing={x.get("provenance",{}).get("message_hash") for x in self.list(session_id,limit=5000)}; added=[]
        for m in transcript or []:
            role=m.get("role","user"); content=str(m.get("content","") or "")
            if not content:continue
            h=self._hash(session_id,role,content)
            if h in existing:continue
            added.append(self.append(session_id,"message",content,{"role":role,"message_hash":h})); existing.add(h)
        return added
