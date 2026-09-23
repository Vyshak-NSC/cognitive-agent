"""Lightweight deterministic controller around the generative agent.
It creates explicit execution state so retrieval, reasoning, action, validation and persistence
are represented as a repeatable lifecycle rather than a single prompt."""
from datetime import datetime, timezone
def now(): return datetime.now(timezone.utc).isoformat()
class CognitiveCycle:
    STAGES=("interpret","assess_state","retrieve","reason","identify_gaps","act","validate","analyze_impact","persist")
    def __init__(self,store,session_id=None):
        self.store=store; self.session_id=session_id; self.state={"cycle_id":now(),"session_id":session_id,"stage":"interpret","history":[]}
    def advance(self,stage,**details):
        self.state["stage"]=stage; self.state["history"].append({"stage":stage,"timestamp":now(),**details})
        return self.state
    def briefing(self,task):
        self.advance("interpret",task=task)
        self.advance("assess_state",current_version=self.store.state_map().get("current_version",0))
        return {"lifecycle":list(self.STAGES),"current_stage":self.state["stage"],"execution_state":self.state}
