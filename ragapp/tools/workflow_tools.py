from ragapp.tools.definitions import Tool
from ragapp.core.agents import AgentStore

def build_workflow_tools(store):
    s=AgentStore(store)
    def describe(agent_id):
        a=s.get(agent_id)
        if not a: return {'status':'error','error':'Agent not found'}
        steps=a.get('workflow_steps',[])
        return {'agent_id':agent_id,'steps':steps,'deterministic_steps':sum(x.get('type','tool')!='inference' for x in steps),'inference_steps':sum(x.get('type')=='inference' for x in steps)}
    return [Tool('describe_agent_workflow','Inspect an agent workflow and its deterministic/inference boundaries.',{'type':'object','properties':{'agent_id':{'type':'string'}},'required':['agent_id']},describe)]
