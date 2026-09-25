from __future__ import annotations
import json, re, uuid
from datetime import datetime, timezone
from pathlib import Path


def _now(): return datetime.now(timezone.utc).isoformat()
def _slug(s):
    s=re.sub(r'[^a-zA-Z0-9_-]+','-',str(s).strip()).strip('-').lower()
    return s or uuid.uuid4().hex[:12]

class AgentStore:
    """Project-scoped persistent agent definitions. No LLM is used here."""
    def __init__(self, store):
        self.path=Path(store.root)/'.system'/'agents.json'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if not self.path.exists(): self._save({"agents":{}})
    def _load(self):
        try: return json.loads(self.path.read_text(encoding='utf-8'))
        except Exception: return {"agents":{}}
    def _save(self,data):
        tmp=self.path.with_suffix('.tmp'); tmp.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8'); tmp.replace(self.path)
    def list(self, enabled_only=False):
        xs=list(self._load().get('agents',{}).values())
        if enabled_only: xs=[x for x in xs if x.get('enabled',True)]
        return sorted(xs,key=lambda x:x.get('name','').lower())
    def get(self, agent_id): return self._load().get('agents',{}).get(agent_id)
    def save(self, spec):
        data=self._load(); agents=data.setdefault('agents',{})
        aid=spec.get('id') or _slug(spec.get('name','agent'))
        old=agents.get(aid,{})
        now=_now()
        obj={
          'id':aid,'name':str(spec.get('name') or old.get('name') or aid),
          'description':str(spec.get('description',old.get('description',''))),
          'objective':str(spec.get('objective',old.get('objective',''))),
          'instructions':list(spec.get('instructions',old.get('instructions',[])) or []),
          'data_sources':list(spec.get('data_sources',old.get('data_sources',[])) or []),
          'output_targets':list(spec.get('output_targets',old.get('output_targets',[])) or []),
          'allowed_tools':list(spec.get('allowed_tools',old.get('allowed_tools',[])) or []),
          'denied_tools':list(spec.get('denied_tools',old.get('denied_tools',[])) or []),
          'workflow_steps':list(spec.get('workflow_steps',old.get('workflow_steps',[])) or []),
          'trigger':dict(spec.get('trigger',old.get('trigger',{'type':'manual'})) or {'type':'manual'}),
          'require_mutation_approval':bool(spec.get('require_mutation_approval',old.get('require_mutation_approval',True))),
          'enabled':bool(spec.get('enabled',old.get('enabled',True))),
          'created_at':old.get('created_at',now),'updated_at':now,
        }
        agents[aid]=obj; self._save(data); return obj
    def delete(self, agent_id):
        data=self._load(); existed=data.get('agents',{}).pop(agent_id,None) is not None; self._save(data); return existed
