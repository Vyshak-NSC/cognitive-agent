import tempfile
from pathlib import Path
from types import SimpleNamespace

from ragapp.chat_sessions import ChatSessionStore
from ragapp.core.vcs import VCSManager


def test_edit_creates_sibling_and_preserves_original():
    root=Path(tempfile.mkdtemp()); chats=ChatSessionStore(SimpleNamespace(sessions=root))
    s=chats.create(); sid=s['id']
    chats.append_turn(sid,{'role':'user','content':'q1'},{'role':'assistant','content':'r1'},turn_id='t1')
    chats.append_turn(sid,{'role':'user','content':'q2'},{'role':'assistant','content':'r2'},turn_id='t2')
    edited=chats.fork_turn(sid,'t2',{'role':'user','content':'q2.2'},{'role':'assistant','content':'r2.2'})
    assert [m['content'] for m in edited['messages']]==['q1','r1','q2.2','r2.2']
    original=chats.set_active_leaf(sid,'t2')
    assert [m['content'] for m in original['messages']]==['q1','r1','q2','r2']
    assert len(original['turns'])==3


def test_project_state_branch_materializes_forward_without_rewriting_history():
    root=Path(tempfile.mkdtemp())
    for area in ('source','workspace','cognition','log'): (root/area).mkdir()
    vcs=VCSManager(SimpleNamespace(root=root))
    f=root/'cognition'/'state.txt'; f.write_text('A'); a=vcs.commit('A'); f.write_text('B'); b=vcs.commit('B')
    result=vcs.materialize_state(a,message='activate A branch')
    assert f.read_text()=='A'
    assert result['restored_from']==a
    assert result['commit'] not in {a,b}
    log=vcs.log(10)
    assert a in log and b in log and result['commit'] in log
