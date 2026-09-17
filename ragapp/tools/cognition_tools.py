import json
from ragapp.tools.definitions import Tool
from ragapp.cognition.world_model import WorldModel
from ragapp.cognition.session_memory import SessionMemory

def build_cognition_tools(store,session_id=None):
    wm=WorldModel(store)
    return [
      Tool("get_cognition_index","Return the compact project cognition index: entity names, summaries, tags, sections, paths and artifact metadata. Use this before requesting large content.",{"type":"object","properties":{}},lambda:store.retrieval_index()),
      Tool("request_cognition_context","Retrieve one or many targeted cognition requests locally. Use metadata/summary/state/section/full detail and timeline/as_of to control how much is loaded. Multiple requests are resolved in one local operation.",{"type":"object","properties":{"requests":{"type":"array","items":{"type":"object","properties":{"entity_id":{"type":"string"},"entity_ids":{"type":"array","items":{"type":"string"}},"name":{"type":"string"},"query":{"type":"string"},"attributes":{"type":"array","items":{"type":"string"}},"sections":{"type":"array","items":{"type":"string"}},"timeline":{"type":"string"},"as_of":{"type":"string"},"detail":{"type":"string","enum":["metadata","summary","state","section","full"]},"include_source":{"type":"boolean"}}}},"max_chars":{"type":"integer"}},"required":["requests"]},lambda requests,max_chars=12000:store.retrieve(requests,max_chars=max_chars)),
      Tool("get_entity_metadata","Return only metadata for named entities so the agent can decide whether a deeper fetch is necessary.",{"type":"object","properties":{"names":{"type":"array","items":{"type":"string"}}},"required":["names"]},lambda names: {"entities": [store.master_metadata().get("entities",{}).get(k) or (store.master_metadata().get("entities",{}).get(store.matching_entities(k)[0]) if store.matching_entities(k) else None) for k in names]}),
      Tool("get_state_map","Compatibility alias for the compact cognition index.",{"type":"object","properties":{}},lambda:store.state_map()),
      Tool("get_ledger","Compatibility view of current states. Prefer request_cognition_context for scoped state.",{"type":"object","properties":{}},lambda:store.ledger()),
      Tool("load_entities","Load exact entity files and directly linked relationships. Use targeted names only.",{"type":"object","properties":{"names":{"type":"array","items":{"type":"string"}}},"required":["names"]},lambda names:store.load_entities(names)),
      Tool("record_fact","Persist a durable fact with provenance/confidence/temporal validity.",{"type":"object","properties":{"claim":{"type":"string"},"status":{"type":"string"},"confidence":{"type":"number"},"evidence_ids":{"type":"array","items":{"type":"string"}},"entities":{"type":"array","items":{"type":"string"}},"valid_from":{"type":"string"},"valid_to":{"type":"string"},"source":{"type":"string"}},"required":["claim"]},lambda **a:wm.add_fact(**a)),
      Tool("record_evidence","Persist evidence and its source locator.",{"type":"object","properties":{"claim":{"type":"string"},"source_file":{"type":"string"},"locator":{"type":"string"},"excerpt":{"type":"string"},"reliability":{"type":"number"},"source_type":{"type":"string"},"source":{"type":"string"}},"required":["claim"]},lambda **a:wm.add_evidence(**a)),
      Tool("record_hypothesis","Persist a testable hypothesis and supporting/disconfirming evidence.",{"type":"object","properties":{"statement":{"type":"string"},"status":{"type":"string"},"confidence":{"type":"number"},"supporting_evidence":{"type":"array","items":{"type":"string"}},"disconfirming_evidence":{"type":"array","items":{"type":"string"}}},"required":["statement"]},lambda **a:wm.add_hypothesis(**a)),
      Tool("record_decision","Persist decision, rationale, alternatives and consequences.",{"type":"object","properties":{"decision":{"type":"string"},"rationale":{"type":"string"},"alternatives":{"type":"array","items":{"type":"string"}},"consequences":{"type":"array","items":{"type":"string"}},"owner":{"type":"string"}},"required":["decision","rationale"]},lambda **a:wm.add_decision(**a)),
      Tool("record_dependency","Persist a relationship/dependency between project elements.",{"type":"object","properties":{"source":{"type":"string"},"target":{"type":"string"},"relation":{"type":"string"},"impact":{"type":"string"}},"required":["source","target","relation"]},lambda **a:wm.add_dependency(**a)),
      Tool("record_change","Persist a change and its affected elements/validation.",{"type":"object","properties":{"description":{"type":"string"},"affected":{"type":"array","items":{"type":"string"}},"caused_by":{"type":"string"},"validation":{"type":"string"}},"required":["description"]},lambda **a:wm.add_change(**a)),
      Tool("get_contradictions","Find candidate contradictions requiring validation.",{"type":"object","properties":{}},lambda:wm.contradictions()),
      Tool("analyze_impact","Return project elements that may be affected by a change using persisted dependencies.",{"type":"object","properties":{"element":{"type":"string"}},"required":["element"]},lambda element:_impact(wm,element)),
      Tool("validate_cognition","Check cognition integrity.",{"type":"object","properties":{}},lambda:_validate(wm)),
      Tool("get_world_model","Return facts, evidence, hypotheses, decisions, dependencies, changes and open questions.",{"type":"object","properties":{"query":{"type":"string"}}},lambda query="":wm.snapshot(query)),
      Tool("distill_session","Persist a durable episode from the current conversation.",{"type":"object","properties":{"transcript":{"type":"array","items":{"type":"object"}}},"required":["transcript"]},lambda transcript:SessionMemory(store).distill(session_id or "unknown",transcript)),
    ]

def _impact(wm,element):
    deps=wm.get("dependencies"); affected=[]; seen={element}; queue=[element]
    while queue:
        cur=queue.pop(0)
        for d in deps.values():
            if d.get("source")==cur and d.get("target") not in seen: seen.add(d.get("target")); queue.append(d.get("target")); affected.append(d)
            elif d.get("target")==cur and d.get("source") not in seen: seen.add(d.get("source")); queue.append(d.get("source")); affected.append(d)
    return {"element":element,"affected":affected}

def _validate(wm):
    m=wm.snapshot(); evidence=set(m.get("evidence",{})); errors=[]
    for fid,f in m.get("facts",{}).items():
        for eid in f.get("evidence_ids",[]):
            if eid not in evidence:errors.append({"fact":fid,"missing_evidence":eid})
    for did,d in m.get("decisions",{}).items():
        if not d.get("rationale"):errors.append({"decision":did,"error":"missing rationale"})
    return {"valid":not errors,"errors":errors,"contradiction_candidates":wm.contradictions()}
