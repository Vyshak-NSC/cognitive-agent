"""Persistent, branch-aware chat-session storage scoped to a project."""
from __future__ import annotations
import json, re, uuid
from datetime import datetime, timezone


def now_iso(): return datetime.now(timezone.utc).isoformat()
def _safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
    return value.strip("._") or "chat"

def _mid(): return uuid.uuid4().hex

class ChatSessionStore:
    SCHEMA_VERSION = 2
    def __init__(self, store):
        self.store=store; self.root=store.sessions; self.root.mkdir(parents=True,exist_ok=True)
    def _path(self, session_id):
        p=(self.root/f"{_safe_name(session_id)}.json").resolve(); p.relative_to(self.root.resolve()); return p
    def _new(self,sid,title="New chat"):
        now=now_iso(); return {"schema_version":2,"id":sid,"title":title,"created_at":now,"updated_at":now,"active_leaf_id":None,"turns":{}}
    def _migrate(self,data):
        if int(data.get("schema_version",1) or 1)>=2 and isinstance(data.get("turns"),dict): return data
        old=list(data.get("messages") or []); new=self._new(data.get("id") or uuid.uuid4().hex,data.get("title") or "New chat")
        new["created_at"]=data.get("created_at",new["created_at"]); parent=None; i=0
        while i<len(old):
            if old[i].get("role")!="user": i+=1; continue
            user=dict(old[i]); assistant=dict(old[i+1]) if i+1<len(old) and old[i+1].get("role")=="assistant" else {"role":"assistant","content":""}
            tid=_mid(); new["turns"][tid]={"id":tid,"parent_id":parent,"created_at":now_iso(),"user":user,"assistant":assistant,"state_before":None,"state_after":None,"effects":{}}
            parent=tid; i+=2
        new["active_leaf_id"]=parent; new["updated_at"]=data.get("updated_at",now_iso()); return new
    def list_sessions(self):
        out=[]
        for p in self.root.glob("*.json"):
            try:
                d=self._migrate(json.loads(p.read_text(encoding="utf-8"))); d["messages"]=self.active_transcript_data(d); out.append(d)
            except (OSError,json.JSONDecodeError): pass
        return sorted(out,key=lambda x:x.get("updated_at",""),reverse=True)
    def create(self,title="New chat"):
        d=self._new(uuid.uuid4().hex,title); return self.save(d)
    def load(self,session_id):
        p=self._path(session_id)
        if not p.exists(): return None
        try:
            d=self._migrate(json.loads(p.read_text(encoding="utf-8")))
            # Persist migration once, while retaining compatibility projection.
            if int(d.get("schema_version",1))==2: self.save(d)
            d["messages"]=self.active_transcript_data(d); return d
        except json.JSONDecodeError: return None
    def save(self,session):
        session=dict(session); session.pop("messages",None); session["schema_version"]=2; session["updated_at"]=now_iso()
        path=self._path(session["id"]); tmp=path.with_suffix(path.suffix+".tmp"); tmp.write_text(json.dumps(session,indent=2,ensure_ascii=False),encoding="utf-8"); tmp.replace(path)
        session["messages"]=self.active_transcript_data(session); return session
    def lineage_ids_data(self,session,leaf_id=None):
        turns=session.get("turns") or {}; cur=leaf_id if leaf_id is not None else session.get("active_leaf_id"); ids=[]; seen=set()
        while cur and cur in turns and cur not in seen:
            seen.add(cur); ids.append(cur); cur=turns[cur].get("parent_id")
        return list(reversed(ids))
    def lineage_ids(self,session_id,leaf_id=None):
        s=self.load(session_id); return self.lineage_ids_data(s,leaf_id) if s else []
    def active_transcript_data(self,session,leaf_id=None):
        out=[]
        for tid in self.lineage_ids_data(session,leaf_id):
            t=session["turns"][tid]
            u=dict(t.get("user") or {}); u.setdefault("role","user"); u["turn_id"]=tid; out.append(u)
            a=dict(t.get("assistant") or {}); a.setdefault("role","assistant"); a["turn_id"]=tid; out.append(a)
        return out
    def active_transcript(self,session_id):
        s=self.load(session_id); return self.active_transcript_data(s) if s else []
    def append_turn(self,session_id,user_message,assistant_message,title_from=None,*,parent_id=None,state_before=None,state_after=None,effects=None,turn_id=None):
        s=self.load(session_id) or self._new(session_id); s.pop("messages",None); tid=turn_id or _mid(); parent_id=s.get("active_leaf_id") if parent_id is None else parent_id
        s["turns"][tid]={"id":tid,"parent_id":parent_id,"created_at":now_iso(),"user":dict(user_message),"assistant":dict(assistant_message),"state_before":state_before,"state_after":state_after,"effects":effects or {}}
        s["active_leaf_id"]=tid
        if title_from and s.get("title")=="New chat": s["title"]=title_from[:60].strip() or "New chat"
        return self.save(s)
    def fork_turn(self,session_id,replaced_turn_id,new_user_message,assistant_message,*,state_before=None,state_after=None,effects=None,turn_id=None,title_from=None):
        s=self.load(session_id)
        if not s or replaced_turn_id not in s.get("turns",{}): raise KeyError(replaced_turn_id)
        parent=s["turns"][replaced_turn_id].get("parent_id")
        return self.append_turn(
            session_id,
            new_user_message,
            assistant_message,
            title_from=title_from,
            parent_id=parent,
            state_before=state_before,
            state_after=state_after,
            effects=effects,
            turn_id=turn_id,
        )
    def set_active_leaf(self,session_id,turn_id):
        s=self.load(session_id)
        if not s or turn_id not in s.get("turns",{}): raise KeyError(turn_id)
        s.pop("messages",None); s["active_leaf_id"]=turn_id; return self.save(s)
    def children_of(self,session_id,parent_id):
        s=self.load(session_id) or {}; return [t for t in (s.get("turns") or {}).values() if t.get("parent_id")==parent_id]
    def delete(self,session_id):
        p=self._path(session_id)
        if p.exists(): p.unlink()
    def rename(self,session_id,title):
        s=self.load(session_id)
        if not s: raise FileNotFoundError(session_id)
        s.pop("messages",None); s["title"]=title.strip() or "New chat"; return self.save(s)
