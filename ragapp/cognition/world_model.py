"""Persistent, domain-independent world model: facts, evidence, hypotheses, decisions, dependencies and contradictions."""
from __future__ import annotations
import json, uuid
from datetime import datetime, timezone
def now(): return datetime.now(timezone.utc).isoformat()
class WorldModel:
    def __init__(self, store):
        self.store=store; self.path=store.cognition/"world_model.json"; self._ensure()
    def _ensure(self):
        if not self.path.exists(): self._write({"facts":{}, "evidence":{}, "hypotheses":{}, "decisions":{}, "dependencies":{}, "contradictions":{}, "open_questions":{}, "changes":[]})
    def _read(self):
        try:return json.loads(self.path.read_text(encoding="utf-8"))
        except:return {"facts":{}, "evidence":{}, "hypotheses":{}, "decisions":{}, "dependencies":{}, "contradictions":{}, "open_questions":{}, "changes":[]}
    def _write(self,x):
        tmp=self.path.with_suffix(".tmp"); tmp.write_text(json.dumps(x,indent=2,ensure_ascii=False),encoding="utf-8"); tmp.replace(self.path)
    def record(self,kind,data,source=None):
        m=self._read(); ident=data.get("id") or uuid.uuid4().hex
        data={**data,"id":ident,"updated_at":now(),"source":source}
        bucket=m.setdefault(kind,{})
        if isinstance(bucket,dict): bucket[ident]=data
        else: raise ValueError(kind)
        self._write(m); self.store.append_event({"type":f"world_model_{kind}","id":ident,"data":data})
        return data
    def get(self,kind,ident=None):
        m=self._read(); return m.get(kind,{}) if ident is None else m.get(kind,{}).get(ident)
    def snapshot(self,query=""):
        m=self._read()
        if not query:return m
        q=query.lower()
        def filt(bucket):
            return {k:v for k,v in bucket.items() if q in json.dumps(v,ensure_ascii=False).lower()}
        return {k:filt(v) if isinstance(v,dict) else v for k,v in m.items()}
    def add_fact(self,claim,status="supported",confidence=None,evidence_ids=None,entities=None,valid_from=None,valid_to=None,source=None):
        return self.record("facts",{"claim":claim,"status":status,"confidence":confidence,"evidence_ids":evidence_ids or [],"entities":entities or [],"valid_from":valid_from,"valid_to":valid_to},source)
    def add_evidence(self,claim,source_file=None,locator=None,excerpt=None,reliability=None,source_type="artifact",source=None):
        return self.record("evidence",{"claim":claim,"source_file":source_file,"locator":locator,"excerpt":excerpt,"reliability":reliability,"source_type":source_type},source)
    def add_hypothesis(self,statement,status="open",confidence=None,supporting_evidence=None,disconfirming_evidence=None):
        return self.record("hypotheses",{"statement":statement,"status":status,"confidence":confidence,"supporting_evidence":supporting_evidence or [],"disconfirming_evidence":disconfirming_evidence or []})
    def add_decision(self,decision,rationale,alternatives=None,consequences=None,owner=None):
        return self.record("decisions",{"decision":decision,"rationale":rationale,"alternatives":alternatives or [],"consequences":consequences or [],"owner":owner})
    def add_dependency(self,source,target,relation,impact=None):
        return self.record("dependencies",{"source":source,"target":target,"relation":relation,"impact":impact})
    def add_change(self,description,affected=None,caused_by=None,validation=None):
        return self.record("changes",{"description":description,"affected":affected or [],"caused_by":caused_by,"validation":validation})
    def contradictions(self):
        m=self._read(); facts=list(m.get("facts",{}).values()); out=[]
        for i,a in enumerate(facts):
            for b in facts[i+1:]:
                if set(a.get("entities",[])) & set(b.get("entities",[])) and a.get("claim") and b.get("claim"):
                    # Explicit contradiction records are preferred; heuristic only flags candidates.
                    if a.get("status")=="supported" and b.get("status")=="supported" and a.get("claim").lower()!=b.get("claim").lower():
                        out.append({"fact_a":a["id"],"fact_b":b["id"],"reason":"same entities with competing supported claims"})
        return out
