from __future__ import annotations
from dataclasses import dataclass

@dataclass
class WorkflowResult:
    completed: bool
    outputs: dict
    calls: list
    inference: dict|None=None


def _resolve(v, outputs):
    if isinstance(v,dict) and set(v)=={'$ref'}:
        cur=outputs
        for p in str(v['$ref']).split('.'):
            if isinstance(cur,dict): cur=cur[p]
            else: raise KeyError(v['$ref'])
        return cur
    if isinstance(v,dict): return {k:_resolve(x,outputs) for k,x in v.items()}
    if isinstance(v,list): return [_resolve(x,outputs) for x in v]
    return v


def run_deterministic_prefix(steps, registry, allowed_tools=None):
    """Run until an explicit inference step. Fully deterministic workflows make zero model calls."""
    outputs={}; calls=[]
    allowed=set(allowed_tools or []) if allowed_tools else None
    for i,step in enumerate(steps or []):
        kind=step.get('type','tool'); sid=step.get('id') or f'step_{i+1}'
        if kind=='inference':
            return WorkflowResult(False,outputs,calls,{'index':i,'step':step})
        if kind=='set':
            outputs[sid]=_resolve(step.get('value'),outputs); continue
        if kind!='tool': raise ValueError(f'Unsupported deterministic workflow step type: {kind}')
        name=step.get('tool')
        if not name: raise ValueError(f'{sid}: tool is required')
        if allowed is not None and name not in allowed: raise PermissionError(f'{sid}: tool {name!r} is not allowed by this agent')
        args=_resolve(step.get('args',{}),outputs)
        result=registry.call(name,args)
        calls.append({'tool':name,'args':args,'result':result,'workflow_step':sid})
        outputs[sid]=result
        if isinstance(result,dict) and result.get('status')=='error': raise RuntimeError(f'{sid}: {result.get("error","tool failed")}')
    return WorkflowResult(True,outputs,calls,None)
