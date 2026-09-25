from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from ragapp.auth.db import initialize_database
from ragapp.execution.project import get_project,list_projects,create_project
from ragapp.workspace.manager import set_current_project
from ragapp.agent.loop import run_agent
from ragapp.agent.run_context import AgentRunContext
from ragapp.tools import build_default_tools
from ragapp.core.drafts import DraftManager
from ragapp.core.approval import ApprovalEngine
from ragapp.core.instructions import InstructionStore
from ragapp.core.vcs import VCSManager
from ragapp.core.agents import AgentStore
from ragapp.cognition.compiler import compile_project
from ragapp.cognition.session_memory import SessionMemory
from ragapp.chat_sessions import ChatSessionStore
import uuid
from ragapp.tools.cognition_tools import _world_model_snapshot, _validate, _impact

app=FastAPI(title='Agentic State Layer API',version='0.1.0')
initialize_database()
class Query(BaseModel): username:str; project_id:str; messages:list[dict]; session_id:str|None=None; agent_id:str|None=None
class Project(BaseModel): username:str; project_id:str
class Reject(BaseModel): reason:str=''
class Instruction(BaseModel): content:str; scope:str='situational'; tagged_entity_id:str|None=None
class DraftEdit(BaseModel): content:str; metadata:dict

def store_for(username,pid): return get_project(username,pid)
@app.get('/health')
def health(): return {'ok':True}
@app.get('/projects/{username}')
def projects(username:str): return list_projects(username)
@app.post('/projects')
def project(p:Project): return {'project_id':create_project(p.username,p.project_id).project_id}
@app.post('/query')
def query(q:Query):
    s=store_for(q.username,q.project_id); set_current_project(s)
    session_id=q.session_id or uuid.uuid4().hex
    chats=ChatSessionStore(s)
    stored=chats.load(session_id)
    transcript=list((stored or {}).get('messages', []))
    # Accept either a latest-message request or a client-supplied transcript
    # without duplicating messages already persisted server-side.
    existing={(m.get('role'), str(m.get('content',''))) for m in transcript if isinstance(m,dict)}
    for msg in q.messages or []:
        key=(msg.get('role'), str(msg.get('content','')))
        if key not in existing:
            transcript.append(msg); existing.add(key)
    try:
        answer,calls,drafts=run_agent(AgentRunContext(
            transcript=transcript,
            tools=build_default_tools(q.username,s,True,session_id=session_id),
            cognition=s,
            project_id=q.project_id,
            session_id=session_id,
            agent_id=q.agent_id,
        ))
    except Exception as e: raise HTTPException(500,str(e))
    user_message=next((m for m in reversed(transcript) if m.get('role')=='user'), {'role':'user','content':''})
    chats.append_turn(
        session_id,
        user_message,
        {'role':'assistant','content':answer,'tool_calls':calls},
        title_from=str(user_message.get('content','')),
    )
    return {'session_id':session_id,'answer':answer,'calls':calls,'drafts':drafts}
@app.get('/drafts/{username}/{project_id}')
def drafts(username,project_id): return DraftManager(store_for(username,project_id)).list()
@app.post('/drafts/{username}/{project_id}/{draft_id}/approve')
def approve(username,project_id,draft_id):
    try: return ApprovalEngine(store_for(username,project_id)).approve(draft_id)
    except Exception as e: raise HTTPException(400,str(e))
@app.post('/drafts/{username}/{project_id}/{draft_id}/reject')
def reject(username,project_id,draft_id,r:Reject):
    try: return ApprovalEngine(store_for(username,project_id)).reject(draft_id,r.reason)
    except Exception as e: raise HTTPException(400,str(e))
@app.put('/drafts/{username}/{project_id}/{draft_id}')
def edit_draft(username,project_id,draft_id,d:DraftEdit):
    dm=DraftManager(store_for(username,project_id)); x=dm.load(draft_id); x['content']=d.content; x['metadata']=d.metadata; return dm.save(x)
@app.get('/instructions/{username}/{project_id}')
def instructions(username,project_id): return InstructionStore(store_for(username,project_id)).list()
@app.post('/instructions/{username}/{project_id}')
def add_instruction(username,project_id,i:Instruction): return {'id':InstructionStore(store_for(username,project_id)).add(i.content,i.scope,i.tagged_entity_id)}
@app.patch('/instructions/{username}/{project_id}/{iid}')
def deactivate_instruction(username,project_id,iid:str):
    x=InstructionStore(store_for(username,project_id)); x.deactivate(iid); return {'ok':True}
@app.get('/git/{username}/{project_id}/log')
def gitlog(username,project_id): return {'log':VCSManager(store_for(username,project_id)).log()}
@app.post('/ingest/{username}/{project_id}')
def ingest(username,project_id): return compile_project(store_for(username,project_id))


@app.get('/cognition/{username}/{project_id}')
def cognition(username,project_id,query:str=''):
    return _world_model_snapshot(store_for(username,project_id), query)
@app.get('/cognition/{username}/{project_id}/validate')
def cognition_validate(username,project_id):
    return _validate(store_for(username,project_id))
@app.get('/cognition/{username}/{project_id}/impact')
def cognition_impact(username,project_id,element:str):
    return _impact(store_for(username,project_id),element)
@app.get('/sessions/{username}/{project_id}')
def sessions(username,project_id):
    from ragapp.chat_sessions import ChatSessionStore
    return ChatSessionStore(store_for(username,project_id)).list_sessions()
@app.get('/sessions/{username}/{project_id}/{session_id}/memory')
def session_memory(username,project_id,session_id):
    return SessionMemory(store_for(username,project_id)).list(session_id)

# ---- Project file VCS API -------------------------------------------------
# These endpoints expose the same deterministic service used by UI/agent tools.
from ragapp.core.project_files import ProjectFileService

class FileWrite(BaseModel):
    content: str
    overwrite: bool = False
    message: str | None = None

class FileMove(BaseModel):
    source_area: str
    source_path: str
    destination_area: str
    destination_path: str
    message: str | None = None

class FileRestore(BaseModel):
    commit: str
    message: str | None = None

class Checkpoint(BaseModel):
    message: str = "Manual project checkpoint"

@app.get('/projects/{username}/{project_id}/files/{area}/history')
def file_history(username:str, project_id:str, area:str, path:str, limit:int=50):
    try:
        return ProjectFileService(store_for(username, project_id)).history(area, path, min(max(limit,1),100))
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get('/projects/{username}/{project_id}/files/{area}/revision')
def file_revision(username:str, project_id:str, area:str, path:str, commit:str):
    try:
        content=ProjectFileService(store_for(username, project_id)).show_revision(area, path, commit)
        return {'area':area,'path':path,'commit':commit,'content':content}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get('/projects/{username}/{project_id}/files/{area}/diff')
def file_diff(username:str, project_id:str, area:str, path:str, revision_a:str, revision_b:str):
    try:
        vcs=VCSManager(store_for(username, project_id))
        return {'diff':vcs.diff(revision_a, revision_b, f'{area}/{path}')}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post('/projects/{username}/{project_id}/files/{area}/restore')
def file_restore(username:str, project_id:str, area:str, path:str, body:FileRestore):
    try:
        return ProjectFileService(store_for(username, project_id)).restore(area, path, body.commit, body.message)
    except Exception as e:
        raise HTTPException(400, str(e))

@app.put('/projects/{username}/{project_id}/files/{area}/text')
def file_write(username:str, project_id:str, area:str, path:str, body:FileWrite):
    try:
        return ProjectFileService(store_for(username, project_id)).write_text(
            area, path, body.content, overwrite=body.overwrite, description=body.message
        )
    except Exception as e:
        raise HTTPException(400, str(e))

@app.delete('/projects/{username}/{project_id}/files/{area}')
def file_delete(username:str, project_id:str, area:str, path:str):
    try:
        return ProjectFileService(store_for(username, project_id)).delete(area, path)
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post('/projects/{username}/{project_id}/files/move')
def file_move(username:str, project_id:str, body:FileMove):
    try:
        return ProjectFileService(store_for(username, project_id)).move(
            body.source_area, body.source_path, body.destination_area, body.destination_path, body.message
        )
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post('/projects/{username}/{project_id}/files/copy')
def file_copy(username:str, project_id:str, body:FileMove):
    try:
        return ProjectFileService(store_for(username, project_id)).copy(
            body.source_area, body.source_path, body.destination_area, body.destination_path, body.message
        )
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post('/projects/{username}/{project_id}/vcs/checkpoint')
def vcs_checkpoint(username:str, project_id:str, body:Checkpoint):
    try:
        return {'commit':VCSManager(store_for(username, project_id)).checkpoint(body.message)}
    except Exception as e:
        raise HTTPException(400, str(e))


# ---- Persistent Agents ----------------------------------------------------
class AgentSpec(BaseModel):
    id: str|None=None
    name: str
    description: str=''
    objective: str=''
    instructions: list[str]=[]
    data_sources: list[str]=[]
    output_targets: list[str]=[]
    allowed_tools: list[str]=[]
    denied_tools: list[str]=[]
    workflow_steps: list[dict]=[]
    trigger: dict={'type':'manual'}
    require_mutation_approval: bool=True
    enabled: bool=True

@app.get('/agents/{username}/{project_id}')
def list_agent_specs(username,project_id): return AgentStore(store_for(username,project_id)).list()

@app.get('/agents/{username}/{project_id}/{agent_id}')
def get_agent_spec(username,project_id,agent_id):
    x=AgentStore(store_for(username,project_id)).get(agent_id)
    if not x: raise HTTPException(404,'Agent not found')
    return x

@app.put('/agents/{username}/{project_id}')
def put_agent_spec(username,project_id,a:AgentSpec): return AgentStore(store_for(username,project_id)).save(a.model_dump())

@app.delete('/agents/{username}/{project_id}/{agent_id}')
def delete_agent_spec(username,project_id,agent_id): return {'deleted':AgentStore(store_for(username,project_id)).delete(agent_id)}
