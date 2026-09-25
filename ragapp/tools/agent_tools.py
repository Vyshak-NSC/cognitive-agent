from ragapp.tools.definitions import Tool
from ragapp.core.agents import AgentStore

def build_agent_tools(store):
    s=AgentStore(store)
    return [
      Tool('list_agents','List persistent agent definitions for this project.',{'type':'object','properties':{}},lambda: {'agents':s.list()}),
      Tool('get_agent','Load one persistent agent definition.',{'type':'object','properties':{'agent_id':{'type':'string'}},'required':['agent_id']},lambda agent_id: s.get(agent_id) or {'status':'error','error':'Agent not found'}),
      Tool('create_or_update_agent','Create or update a persistent agent. workflow_steps are typed steps: tool, set, or inference. Use deterministic tool/set steps wherever possible; inference must be explicit.',{'type':'object','properties':{'spec':{'type':'object'}},'required':['spec']},lambda spec: s.save(spec)),
      Tool('delete_agent','Delete a persistent agent definition.',{'type':'object','properties':{'agent_id':{'type':'string'}},'required':['agent_id']},lambda agent_id: {'deleted':s.delete(agent_id)}),
    ]
