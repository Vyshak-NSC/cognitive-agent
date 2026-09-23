from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from ragapp.auth.db import initialize_database
from ragapp.execution.project import get_project,list_projects,create_project
from ragapp.workspace.manager import set_current_project
from ragapp.agent.loop import run_agent
from ragapp.tools import build_default_tools
from ragapp.core.drafts import DraftManager
from ragapp.core.approval import ApprovalEngine
from ragapp.core.instructions import InstructionStore
from ragapp.core.vcs import VCSManager
from ragapp.cognition.compiler import compile_project
from ragapp.cognition.session_memory import SessionMemory
from ragapp.tools.cognition_tools import _world_model_snapshot, _validate, _impact

app=FastAPI(title='Agentic State Layer API',version='0.1.0')
initialize_database()
class Query(BaseModel): username:str; project_id:str; messages:list[dict]
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
    try: answer,calls,drafts=run_agent(q.messages,build_default_tools(q.username,s,True,session_id='api'),s,q.project_id,session_id='api')
    except Exception as e: raise HTTPException(500,str(e))
    return {'answer':answer,'calls':calls,'drafts':drafts}
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
