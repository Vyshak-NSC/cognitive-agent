from ragapp.cognition.store import CognitionStore
from ragapp.workspace.manager import set_current_project
from ragapp.agent.loop import run_agent
from ragapp.agent.run_context import AgentRunContext
from ragapp.tools import build_default_tools

def main():
    username=input('Username: ').strip().lower(); project=input('Project [default]: ').strip() or 'default'; store=CognitionStore(username,project); set_current_project(store)
    if not store.exists(): print('Project is not compiled. Use the Streamlit interface to add source artifacts and compile cognition.'); return
    transcript=[]; print('Type exit to quit.')
    while True:
        q=input('\nYou: ').strip()
        if q.lower() in {'exit','quit'}: break
        transcript.append({'role':'user','content':q}); answer,calls,drafts=run_agent(AgentRunContext(transcript=transcript, tools=build_default_tools(username,store), cognition=store, project_id=project)); print('\nAgent:',answer); transcript.append({'role':'assistant','content':answer}); print(f'[tool calls: {len(calls)}]')
        if drafts: print(f'[drafts created: {", ".join(d["id"][:8] for d in drafts)} — review in the Streamlit Review tab]')
if __name__=='__main__': main()
