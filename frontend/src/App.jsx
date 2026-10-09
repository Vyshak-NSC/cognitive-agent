import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import MarkdownContent from './components/Markdown';
import FilePreview from './components/FilePreview';
import { Archive, ArrowDownToLine, ArrowUp, Bot, Brain, ChevronDown, ChevronRight, ClipboardList, Copy, Edit3, Eye, FileCode2, FilePlus2, FileText, Folder, FolderOpen, GitBranch, History, LogOut, MessageCircle, MoreHorizontal, Pencil, Play, Plus, RefreshCw, Save, Search, Settings as SettingsIcon, ShieldCheck, Trash2, Upload, X, Zap } from 'lucide-react';
import { api, streamChat } from './api';

// Formats the text editor cannot round-trip: shown in the preview pane only.
const PREVIEW_ONLY = /\.(docx|pptx|xlsx|xls|pdf|png|jpe?g|gif|webp|bmp|ico)$/i;

const NAV = [
  ['chat', 'Chat', MessageCircle],
  ['files', 'Files', Folder],
  ['cognition', 'Cognition', Brain],
  ['review', 'Review', ClipboardList],
  ['instructions', 'Instructions', ClipboardList],
  ['history', 'History', History],
  ['settings', 'Settings', SettingsIcon],
];

function usePersisted(key, initial) {
  const [value, setValue] = useState(() => localStorage.getItem(key) ?? initial);
  useEffect(() => localStorage.setItem(key, value), [key, value]);
  return [value, setValue];
}

function fmtBytes(n = 0) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
function shortId(s = '') { return s.slice(0, 8); }
function safeArray(x) { return Array.isArray(x) ? x : []; }

function Toast({ message, kind = 'info', onClose }) {
  if (!message) return null;
  return <div className={`toast ${kind}`} onClick={onClose}><span>{message}</span><X size={15}/></div>;
}
function Modal({ title, children, onClose, wide = false }) {
  useEffect(() => { const h = e => e.key === 'Escape' && onClose(); window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h); }, [onClose]);
  return <div className="modal-backdrop" onMouseDown={e => e.target === e.currentTarget && onClose()}>
    <div className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true">
      <div className="modal-head"><h3>{title}</h3><button className="icon-btn" onClick={onClose} data-tip="Close" data-tip-align="end" aria-label="Close"><X size={17}/></button></div>
      <div className="modal-body">{children}</div>
    </div>
  </div>;
}
function JsonBlock({ value }) {
  return <pre className="json-block">{JSON.stringify(value, null, 2)}</pre>;
}
function EmptyState({ icon: Icon = MessageCircle, title, text }) {
  return <div className="empty-state"><div className="empty-icon"><Icon size={22}/></div><h3>{title}</h3><p>{text}</p></div>;
}
function Button({ children, variant = 'ghost', icon: Icon, ...props }) {
  return <button className={`btn ${variant}`} {...props}>{Icon && <Icon size={15}/>}<span>{children}</span></button>;
}
function Toggle({ value, onChange, label }) {
  return <label className="toggle-row"><button type="button" className={`toggle ${value ? 'on' : ''}`} onClick={() => onChange(!value)}><span/></button><span>{label}</span></label>;
}

export default function App() {
  const [username, setUsername] = usePersisted('cpa.username', import.meta.env.VITE_DEFAULT_USERNAME || '');
  const [project, setProject] = usePersisted('cpa.project', '');
  const [sessionId, setSessionId] = usePersisted('cpa.session', '');
  const [section, setSection] = usePersisted('cpa.section', 'chat');
  const [agentId, setAgentId] = usePersisted('cpa.agent', '');
  const [previewEnabled, setPreviewEnabled] = usePersisted('cpa.preview', 'false');
  const [showExecution, setShowExecution] = usePersisted('cpa.execution', 'true');
  const [projects, setProjects] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [agents, setAgents] = useState([]);
  const [projectStatus, setProjectStatus] = useState(null);
  const [pendingDrafts, setPendingDrafts] = useState(0);
  const [toast, setToast] = useState(null);
  const [busy, setBusy] = useState(false);
  const [projectModal, setProjectModal] = useState(null);
  const [sessionModal, setSessionModal] = useState(null);

  const notify = (message, kind = 'info') => { setToast({ message, kind }); setTimeout(() => setToast(null), 3600); };
  const onError = e => notify(e?.message || String(e), 'error');

  const refreshShell = async () => {
    if (!username) return;
    try {
      const list = await api.projects(username);
      setProjects(list || []);
      if (!project || !list?.includes(project)) setProject(list?.[0] || '');
    } catch (e) { onError(e); }
  };
  const refreshProject = async (p = project) => {
    if (!username || !p) return;
    try {
      const [status, ss, aa, dd] = await Promise.all([
        api.project(username, p), api.sessions(username, p), api.agents(username, p), api.drafts(username, p)
      ]);
      setProjectStatus(status); setSessions(ss || []); setAgents(aa || []); setPendingDrafts((dd || []).filter(d => d.status === 'pending').length);
      if (!sessionId || !(ss || []).some(s => s.id === sessionId)) setSessionId(ss?.[0]?.id || '');
      if (agentId && !(aa || []).some(a => a.id === agentId && a.enabled !== false)) setAgentId('');
    } catch (e) { onError(e); }
  };
  useEffect(() => { refreshShell(); }, [username]);
  useEffect(() => { if (project) refreshProject(project); }, [project]);
  useEffect(() => { document.title = project ? `CPA · ${project}` : 'Cognitive Persistence Agent'; }, [project]);

  const logout = () => {
    setUsername(''); setProject(''); setSessionId(''); setAgentId(''); setSection('chat');
    setProjects([]); setSessions([]); setAgents([]); setProjectStatus(null); setPendingDrafts(0);
    setProjectModal(null); setSessionModal(null);
  };
  const chooseProject = async p => { setProject(p); setSessionId(''); setSection('chat'); };
  const createProject = async name => {
    if (!name.trim()) return;
    setBusy(true); try { const r = await api.createProject(username, name.trim()); setProject(r.project_id); setSessionId(''); await refreshShell(); notify('Project created', 'success'); setProjectModal(null); } catch (e) { onError(e); } finally { setBusy(false); }
  };
  const renameProject = async name => {
    if (!name.trim() || name.trim() === project) return setProjectModal(null);
    setBusy(true); try { const r = await api.renameProject(username, project, name.trim()); setProject(r.project_id); await refreshShell(); await refreshProject(r.project_id); notify('Project renamed', 'success'); setProjectModal(null); } catch (e) { onError(e); } finally { setBusy(false); }
  };
  const deleteProject = async () => {
    setBusy(true); try { const r = await api.deleteProject(username, project); const next = r.projects?.[0] || ''; setProject(next); setSessionId(''); await refreshShell(); notify('Project deleted', 'success'); setProjectModal(null); } catch (e) { onError(e); } finally { setBusy(false); }
  };
  const createSession = async title => {
    setBusy(true); try { const s = await api.createSession(username, project, title); setSessionId(s.id); await refreshProject(); setSessionModal(null); notify('New chat created', 'success'); } catch (e) { onError(e); } finally { setBusy(false); }
  };
  const renameSession = async title => {
    setBusy(true); try { await api.renameSession(username, project, sessionId, title); await refreshProject(); setSessionModal(null); notify('Chat renamed', 'success'); } catch (e) { onError(e); } finally { setBusy(false); }
  };
  const deleteSession = async () => {
    setBusy(true); try { const r = await api.deleteSession(username, project, sessionId); setSessions(r.sessions || []); setSessionId(r.sessions?.[0]?.id || ''); notify('Chat deleted', 'success'); setSessionModal(null); } catch (e) { onError(e); } finally { setBusy(false); }
  };

  if (!username) return <Login onLogin={setUsername}/>;

  if (!project) return <div className="app"><Topbar username={username} onLogout={logout} project="" projects={projects} onProject={chooseProject} onProjectAction={setProjectModal} section={section} setSection={setSection} pendingDrafts={pendingDrafts}/><main className="main"><EmptyState icon={FolderOpen} title="No project selected" text="Create a project or choose one from the top bar to begin."/><Button variant="primary" icon={Plus} onClick={() => setProjectModal('create')}>Create project</Button></main>{projectModal && <ProjectModal mode={projectModal} project={project} onClose={() => setProjectModal(null)} onCreate={createProject} onRename={renameProject} onDelete={deleteProject} busy={busy}/>}<Toast {...toast} onClose={() => setToast(null)}/></div>;

  return <div className="app">
    <Topbar username={username} onLogout={logout} project={project} projects={projects} onProject={chooseProject} onProjectAction={setProjectModal} section={section} setSection={setSection} pendingDrafts={pendingDrafts}/>
    <div className="utilitybar">
      <div className="utility-left">
        <Toggle value={previewEnabled === 'true'} onChange={v => setPreviewEnabled(String(v))} label="Show preview"/>
        <Toggle value={showExecution === 'true'} onChange={v => setShowExecution(String(v))} label="Show execution"/>
        <span className="dot-divider"/>
        <span className="muted">Agent</span>
        <select className="compact-select" value={agentId} onChange={e => setAgentId(e.target.value)}><option value="">Default agent</option>{agents.filter(a => a.enabled !== false).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
      </div>
      <div className="utility-right">
        <span className="status-dot"/> <span className="muted">{projectStatus?.source_file_count + projectStatus?.workspace_file_count || 0} files</span>
        <span className="muted">{projectStatus?.counts?.entities || 0} entities</span>
      </div>
    </div>
    <main className={`main ${section === 'chat' || section === 'files' ? 'fill' : ''}`}>
      {section === 'chat' && <ChatView username={username} project={project} sessionId={sessionId} sessions={sessions} agentId={agentId} showExecution={showExecution === 'true'} previewEnabled={previewEnabled === 'true'} setSessionId={setSessionId} refreshProject={refreshProject} onError={onError} notify={notify} setSessionModal={setSessionModal}/>} 
      {section === 'files' && <FilesView username={username} project={project} notify={notify} onError={onError} refreshProject={refreshProject}/>} 
      {section === 'cognition' && <CognitionView username={username} project={project} notify={notify} onError={onError}/>} 
      {section === 'review' && <ReviewView username={username} project={project} notify={notify} onError={onError}/>} 
      {section === 'instructions' && <InstructionsView username={username} project={project} notify={notify} onError={onError}/>} 
      {section === 'history' && <HistoryView username={username} project={project} notify={notify} onError={onError}/>} 
      {section === 'settings' && <SettingsView username={username} project={project} notify={notify} onError={onError}/>} 
    </main>
    {projectModal && <ProjectModal mode={projectModal} project={project} onClose={() => setProjectModal(null)} onCreate={createProject} onRename={renameProject} onDelete={deleteProject} busy={busy}/>} 
    {sessionModal && <SessionModal mode={sessionModal} onClose={() => setSessionModal(null)} onCreate={createSession} onRename={renameSession} onDelete={deleteSession} busy={busy}/>} 
    <Toast {...toast} onClose={() => setToast(null)}/>
  </div>;
}

function Topbar({ username, onLogout, project, projects, onProject, onProjectAction, section, setSection, pendingDrafts }) {
  return <header className="topbar">
    <div className="brand"><div className="brand-mark"><Brain size={20}/></div><div><strong>Cognitive Persistence</strong><small>Persistent project cognition</small></div></div>
    <nav className="navtabs">{NAV.map(([id,label,Icon]) => <button key={id} className={section === id ? 'active' : ''} onClick={() => setSection(id)} data-tip={label} aria-label={label}><Icon size={16}/><span>{label}</span>{id === 'review' && pendingDrafts > 0 && <b className="badge">{pendingDrafts}</b>}</button>)}</nav>
    <div className="top-actions">
      <select className="top-select" value={project} onChange={e => onProject(e.target.value)} aria-label="Project">{projects.map(p => <option key={p}>{p}</option>)}</select>
      <ProjectMenu hasProject={!!project} onAction={onProjectAction}/>
      <div className="user-chip"><span className="avatar">{username[0].toUpperCase()}</span><span>{username}</span><button className="icon-btn" onClick={onLogout} data-tip="Log out" data-tip-align="end" aria-label="Log out"><LogOut size={16}/></button></div>
    </div>
  </header>;
}

function Login({ onLogin }) {
  const [name, setName] = useState('');
  return <div className="login"><form onSubmit={e => { e.preventDefault(); name.trim() && onLogin(name.trim()); }}>
    <div className="brand"><div className="brand-mark"><Brain size={20}/></div><div><strong>Cognitive Persistence</strong><small>Persistent project cognition</small></div></div>
    <h1>Sign in</h1>
    <label className="muted">Username<input autoFocus value={name} onChange={e => setName(e.target.value)} placeholder="Your username"/></label>
    <Button variant="primary" type="submit" disabled={!name.trim()}>Continue</Button>
  </form></div>;
}

function ProjectMenu({ onAction, hasProject }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const close = e => { if (!ref.current?.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);
  const pick = mode => { setOpen(false); onAction(mode); };
  return <div className="menu-wrap" ref={ref}>
    <button className="btn icon-only" onClick={() => setOpen(o => !o)} aria-haspopup="menu" aria-expanded={open} aria-label="Project actions" data-tip={open ? undefined : 'Project actions'}><MoreHorizontal size={16}/></button>
    {open && <div className="menu" role="menu">
      <button role="menuitem" onClick={() => pick('create')}><Plus size={15}/>New project</button>
      <button role="menuitem" disabled={!hasProject} onClick={() => pick('rename')}><Pencil size={15}/>Rename project</button>
      <hr/>
      <button role="menuitem" className="danger" disabled={!hasProject} onClick={() => pick('delete')}><Trash2 size={15}/>Delete project</button>
    </div>}
  </div>;
}

function ProjectModal({ mode, project, onClose, onCreate, onRename, onDelete, busy }) {
  const [name, setName] = useState(mode === 'rename' ? project : '');
  if (mode === 'delete') return <Modal title="Delete project" onClose={onClose}>
    <p>Delete <strong>{project}</strong> permanently? Its files, chats and cognition are removed and this cannot be undone.</p>
    <div className="modal-actions"><Button onClick={onClose}>Cancel</Button><Button variant="danger" icon={Trash2} disabled={busy} onClick={onDelete}>Delete project</Button></div>
  </Modal>;
  const creating = mode === 'create';
  return <Modal title={creating ? 'Create new project' : 'Rename project'} onClose={onClose}>
    <p className="muted">{creating ? 'Create a project and configure its provider later from Settings.' : `Rename ${project}. Existing cognition, files and sessions stay with the project.`}</p>
    <label>Project name<input autoFocus value={name} onChange={e => setName(e.target.value)} onKeyDown={e => e.key === 'Enter' && name.trim() && (creating ? onCreate(name) : onRename(name))} placeholder="e.g. invoice_pipeline"/></label>
    <div className="modal-actions"><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || !name.trim()} onClick={() => creating ? onCreate(name) : onRename(name)}>{creating ? 'Create project' : 'Save name'}</Button></div>
  </Modal>;
}
function SessionModal({ mode, onClose, onCreate, onRename, onDelete, busy }) {
  const [title, setTitle] = useState('');
  if (mode === 'delete') return <Modal title="Delete chat" onClose={onClose}><p>Delete this chat permanently?</p><div className="modal-actions"><Button onClick={onClose}>Cancel</Button><Button variant="danger" disabled={busy} onClick={onDelete}>Delete chat</Button></div></Modal>;
  return <Modal title={mode === 'new' ? 'New chat' : 'Rename chat'} onClose={onClose}><label>Session name<input autoFocus value={title} onChange={e => setTitle(e.target.value)} placeholder="New chat"/></label><div className="modal-actions"><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy} onClick={() => mode === 'new' ? onCreate(title) : onRename(title)}>Save</Button></div></Modal>;
}

function PreviewPane({ username, project }) {
  const [files, setFiles] = useState([]);
  const [err, setErr] = useState('');
  const [sel, setSel] = useState('');
  useEffect(() => {
    let live = true;
    api.files(username, project).then(f => {
      if (!live) return;
      const visible = (f || []).filter(x => !x.path.split('/').some(s => s === '.system' || s === 'drafts'));
      setFiles(visible); setSel(s => s && visible.some(x => `${x.area}|${x.path}` === s) ? s : (visible[0] ? `${visible[0].area}|${visible[0].path}` : ''));
    }).catch(e => live && setErr(e.message));
    return () => { live = false; };
  }, [username, project]);
  const [area, ...rest] = sel.split('|'); const path = rest.join('|');
  return <aside className="preview-pane">
    <select value={sel} onChange={e => setSel(e.target.value)} aria-label="File to preview">{files.map(f => <option key={`${f.area}|${f.path}`} value={`${f.area}|${f.path}`}>[{f.area}] {f.path}</option>)}</select>
    {err ? <pre className="preview-error">{err}</pre> : sel ? <FilePreview username={username} project={project} area={area} path={path}/> : <EmptyState icon={FileText} title="No project files yet" text="Files the agent creates will show up here."/>}
  </aside>;
}

function ChatView({ username, project, sessionId, sessions, agentId, showExecution, setSessionId, refreshProject, onError, notify, previewEnabled, setSessionModal }) {
  const [data, setData] = useState(null);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [plan, setPlan] = useState(null);
  const [done, setDone] = useState(0);
  const [liveText, setLiveText] = useState('');
  const [editing, setEditing] = useState(null);
  const scrollRef = useRef(null), inputRef = useRef(null), stick = useRef(true), abortRef = useRef(null);

  const load = async () => { if (!sessionId) return; try { setData(await api.session(username, project, sessionId)); } catch (e) { onError(e); } };
  useEffect(() => { stick.current = true; load(); }, [username, project, sessionId]);
  useEffect(() => () => abortRef.current?.abort(), []);

  const turns = data?.turns || {};
  const lineage = (() => { const out = []; const seen = new Set(); let id = data?.active_leaf_id; while (id && !seen.has(id)) { seen.add(id); out.unshift(id); id = turns[id]?.parent_id || null; } return out; })();
  const visible = lineage.map(id => turns[id]).filter(Boolean);

  // Follow new output only while the reader is at the bottom; never yank them back up.
  const onScroll = () => { const el = scrollRef.current; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 96; };
  useLayoutEffect(() => { const el = scrollRef.current; if (el && stick.current) el.scrollTop = el.scrollHeight; }, [visible.length, liveText, sending, data]);
  useLayoutEffect(() => { const el = inputRef.current; if (!el) return; el.style.height = 'auto'; el.style.height = `${Math.min(el.scrollHeight + 2, Math.min(220, window.innerHeight * 0.3))}px`; }, [input]);

  const send = async (text, replacingTurnId = null) => {
    if (!text.trim() || sending || !sessionId) return;
    setSending(true); setPlan(null); setDone(0); setLiveText(''); stick.current = true;
    abortRef.current = new AbortController();
    try {
      let result;
      await streamChat({ username, project_id: project, session_id: sessionId, text: text.trim(), replacing_turn_id: replacingTurnId, agent_id: agentId || null }, {
        onPlan: p => setPlan(p.sections || []),
        onSection: e => { setLiveText(prev => `${prev ? `${prev}\n\n` : ''}${e.content || ''}`); setDone(n => n + 1); },
        onComplete: r => { result = r; },
        onError: e => { throw new Error(e.detail || 'Agent execution failed'); },
      }, abortRef.current.signal);
      if (!result) throw new Error('The API closed the stream without a final response.');
      setInput(''); setEditing(null); await load(); await refreshProject(); notify('Turn completed', 'success');
    } catch (e) { if (e.name !== 'AbortError') onError(e); }
    finally { setSending(false); }
  };
  const activate = async tid => { try { await api.activateTurn(username, project, sessionId, tid); await load(); notify('Branch activated', 'success'); } catch (e) { onError(e); } };

  return <div className={`chat-page ${previewEnabled ? 'with-preview' : ''}`}>
    <h1 className="sr-only">Cognitive Persistence Agent</h1>
    <div className="chat-toolbar">
      <select value={sessionId} onChange={e => setSessionId(e.target.value)} aria-label="Chat session">{sessions.map(s => <option key={s.id} value={s.id}>{s.title || 'New chat'}</option>)}</select>
      <Button icon={Plus} onClick={() => setSessionModal('new')}>New chat</Button>
      <Button icon={Pencil} disabled={!sessionId} onClick={() => setSessionModal('rename')}>Rename</Button>
      <Button variant="danger" icon={Trash2} disabled={!sessionId} onClick={() => setSessionModal('delete')}>Delete</Button>
    </div>
    <div className="chat-split">
      <div className="chat-shell">
        <div className="chat-scroll" ref={scrollRef} onScroll={onScroll}>
            {!visible.length && !sending && <div className="chat-empty"><Zap size={24}/><div><strong>Start a task.</strong><p>You can ask the agent to create, inspect, edit, move, copy, or delete project files without uploading anything first.</p></div></div>}
            {visible.map((turn, idx) => <Turn key={turn.id || idx} turn={turn} allTurns={turns} showExecution={showExecution} onEdit={() => setEditing({ id: turn.id, text: turn.user?.content || '' })} onActivate={activate}/>)}
            {sending && <div className="msg agent"><div className="msg-avatar"><Bot size={15}/></div><div className="bubble"><div className="live-label"><span className="spinner"/>Executing…</div>{plan && <div className="plan"><strong>Planned sections</strong>{plan.map((x, i) => <div key={i} className={i < done ? 'done' : ''}><span>{i < done ? '✓' : '•'}</span> {x}</div>)}</div>}<MarkdownContent>{liveText || 'Preparing the agent…'}</MarkdownContent></div></div>}
        </div>
        {editing && <div className="edit-dock"><textarea value={editing.text} onChange={e => setEditing({ ...editing, text: e.target.value })}/><div><Button onClick={() => setEditing(null)}>Cancel</Button><Button variant="primary" onClick={() => send(editing.text, editing.id)}>Submit edit</Button></div></div>}
        <form className="chat-input" onSubmit={e => { e.preventDefault(); send(input); }}><textarea ref={inputRef} rows={1} value={input} onChange={e => setInput(e.target.value)} placeholder="Ask a question or give the agent a task…" onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(input); } }}/><button disabled={sending || !input.trim()} aria-label="Send" data-tip="Send" data-tip-pos="top" data-tip-align="end"><ArrowUp size={18}/></button></form>
      </div>
      {previewEnabled && <PreviewPane username={username} project={project}/>}
    </div>
  </div>;
}

function Turn({ turn, allTurns, showExecution, onEdit, onActivate }) {
  const [traceOpen, setTraceOpen] = useState(false);
  const siblings = Object.values(allTurns || {}).filter(x => x.parent_id === turn.parent_id);
  const index = siblings.findIndex(x => x.id === turn.id);
  return <div>
    <div className="msg user"><div className="bubble"><div className="bubble-head"><span className="msg-role">You</span>{siblings.length > 1 && <span className="branch-count"><GitBranch size={13}/>{index + 1}/{siblings.length}</span>}<button className="icon-btn" onClick={onEdit} data-tip="Edit message" data-tip-align="end" aria-label="Edit message"><Edit3 size={14}/></button></div><MarkdownContent>{turn.user?.content}</MarkdownContent></div></div>
    <div className="msg agent"><div className="msg-avatar"><Bot size={15}/></div><div className="bubble"><div className="msg-role">Agent</div><MarkdownContent>{turn.assistant?.content || ''}</MarkdownContent>{showExecution && (turn.assistant?.execution_trace || turn.assistant?.tool_calls?.length > 0) && <div className="trace-box"><button className="trace-head" onClick={() => setTraceOpen(!traceOpen)}>{traceOpen ? <ChevronDown size={15}/> : <ChevronRight size={15}/>} Execution trace {turn.assistant?.execution_trace?.events ? `· ${turn.assistant.execution_trace.events.length} events` : `· ${turn.assistant?.tool_calls?.length || 0} tool calls`}</button>{traceOpen && <div className="trace-body">{turn.assistant?.execution_trace ? safeArray(turn.assistant.execution_trace.events).map((e,i)=><div className="trace-event" key={i}><strong>{e.seq ?? i+1}. {String(e.type || 'event').replaceAll('_',' ')}</strong>{e.elapsed_ms != null && <span>{e.elapsed_ms} ms</span>}<JsonBlock value={e.data || {}}/></div>) : safeArray(turn.assistant?.tool_calls).map((c,i)=><div className="trace-event" key={i}><strong>Tool call {i+1}: {c.tool || 'unknown'}</strong><JsonBlock value={c.args}/>{c.result != null && <JsonBlock value={c.result}/>}</div>)}</div>}</div>}{siblings.length > 1 && <select className="branch-select" value={turn.id} onChange={e => onActivate(e.target.value)}>{siblings.map(x => <option key={x.id} value={x.id}>{(x.user?.content || x.id).slice(0, 50)}</option>)}</select>}</div></div>
  </div>;
}

function FilesView({ username, project, notify, onError, refreshProject }) {
  const [files, setFiles] = useState([]); const [area, setArea] = useState('all'); const [query, setQuery] = useState(''); const [selected, setSelected] = useState(null); const [content, setContent] = useState(''); const [dirty, setDirty] = useState(false); const [modal, setModal] = useState(null); const [history, setHistory] = useState([]);
  const [mode, setMode] = useState('edit');
  const [editable, setEditable] = useState(true);
  const load = async () => { try { const f = await api.files(username, project, area === 'all' ? undefined : area); setFiles(f || []); } catch(e) { onError(e); } };
  useEffect(() => { load(); }, [username, project, area]);
  const filtered = files.filter(f => `${f.area}/${f.path}`.toLowerCase().includes(query.toLowerCase()));
  const open = async f => {
    setSelected(f); setContent(''); setDirty(false);
    if (PREVIEW_ONLY.test(f.path)) { setEditable(false); setMode('preview'); return; }
    setEditable(true); setMode('edit');
    try { const r = await api.fileText(username, project, f.area, f.path); setContent(r.content); setDirty(false); } catch(e) { setEditable(false); setMode('preview'); } };
  const save = async () => { if (!selected) return; try { await api.writeFile(username, project, selected.area, selected.path, { content, overwrite: true, message: `Edit ${selected.path}` }); setDirty(false); await load(); notify('File saved', 'success'); } catch(e) { onError(e); } };
  const newFile = async ({ area: a, path, content: c }) => { try { await api.writeFile(username, project, a, path, { content: c || '', overwrite: false, message: `Create ${path}` }); await load(); setModal(null); notify('File created', 'success'); } catch(e) { onError(e); } };
  const remove = async f => { if (!confirm(`Delete ${f.area}/${f.path}?`)) return; try { await api.deleteFile(username, project, f.area, f.path); if (selected?.path === f.path && selected?.area === f.area) { setSelected(null); setContent(''); } await load(); notify('File deleted', 'success'); } catch(e) { onError(e); } };
  const upload = async ({ area: a, folder, overwrite, picked }) => {
    try {
      const r = await api.uploadFiles(username, project, a, { files: picked, folder, overwrite });
      await load(); await refreshProject(); setModal(null); notify(`${r.files.length} file${r.files.length === 1 ? '' : 's'} uploaded`, 'success');
    } catch (e) { onError(e); }
  };
  const loadHistory = async f => { try { const h = await api.fileHistory(username, project, f.area, f.path); setHistory(h || []); setModal({ type:'history', file:f }); } catch(e) { onError(e); } };
  const moveCopy = async (f, copy) => setModal({ type: copy ? 'copy' : 'move', file:f });
  const performMoveCopy = async (body, copy) => { try { await (copy ? api.copyFile(username, project, body) : api.moveFile(username, project, body)); await load(); setModal(null); notify(copy ? 'File copied' : 'File moved', 'success'); } catch(e) { onError(e); } };
  return <div className="page"><div className="page-head"><div><h2>Files</h2><p>General-purpose project file management across Source and Workspace.</p></div><div className="row-actions"><Button icon={RefreshCw} onClick={load}>Refresh</Button><Button icon={Upload} onClick={() => setModal({type:'upload'})}>Upload</Button><Button variant="primary" icon={FilePlus2} onClick={() => setModal({type:'new'})}>New file</Button></div></div>
    <div className="files-layout"><aside className="file-sidebar"><div className="segmented"><button className={area==='all'?'active':''} onClick={()=>setArea('all')}>All</button><button className={area==='source'?'active':''} onClick={()=>setArea('source')}>Source</button><button className={area==='workspace'?'active':''} onClick={()=>setArea('workspace')}>Workspace</button></div><div className="search"><Search size={15}/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Filter files…"/></div><div className="file-list">{filtered.map(f=><button key={`${f.area}/${f.path}`} className={`file-row ${selected?.path===f.path&&selected?.area===f.area?'selected':''}`} onClick={()=>open(f)}><FileIcon suffix={f.suffix}/><span><b>{f.path.split('/').pop()}</b><small>{f.area} · {fmtBytes(f.size)}</small></span></button>)}{!filtered.length&&<div className="muted pad">No files found.</div>}</div></aside><section className="editor-panel">{selected ? <><div className="editor-head"><div><strong>{selected.path}</strong><span>{selected.area} · {fmtBytes(selected.size)}</span></div><div className="row-actions">{mode==='preview'?<Button icon={Pencil} disabled={!editable} onClick={()=>setMode('edit')}>Edit</Button>:<Button icon={Eye} onClick={()=>setMode('preview')}>Preview</Button>}<Button icon={History} onClick={()=>loadHistory(selected)}>History</Button><Button icon={Copy} onClick={()=>moveCopy(selected,true)}>Copy</Button><Button icon={ArrowDownToLine} onClick={()=>moveCopy(selected,false)}>Move</Button><Button variant="danger" icon={Trash2} onClick={()=>remove(selected)}>Delete</Button><Button variant="primary" icon={Save} disabled={!dirty} onClick={save}>Save</Button></div></div>{mode==='preview'?<FilePreview username={username} project={project} area={selected.area} path={selected.path} text={dirty?content:undefined}/>:<textarea className="code-editor" value={content} onChange={e=>{setContent(e.target.value);setDirty(true)}} spellCheck="false"/>}</> : <EmptyState icon={FileText} title="Select a file" text="Choose a Source or Workspace file to inspect and edit it."/>}</section></div>
    {modal?.type==='upload' && <UploadModal defaultArea={area==='workspace'?'workspace':'source'} onClose={()=>setModal(null)} onSubmit={upload}/>} {modal?.type==='new' && <FileModal title="New file" onClose={()=>setModal(null)} onSubmit={newFile}/>} {modal?.type==='history' && <HistoryModal file={modal.file} history={history} onClose={()=>setModal(null)} onRevision={async c=>{try{const r=await api.fileRevision(username,project,modal.file.area,modal.file.path,c);setContent(r.content);setDirty(true);setMode('edit');setModal(null);notify('Revision loaded into editor')}catch(e){onError(e)}}}/>} {modal?.type==='move'||modal?.type==='copy' ? <MoveModal file={modal.file} copy={modal.type==='copy'} onClose={()=>setModal(null)} onSubmit={b=>performMoveCopy(b,modal.type==='copy')}/>:null}
  </div>;
}
function FileIcon({suffix}) { return suffix === '.py' || suffix === '.js' || suffix === '.ts' ? <FileCode2 size={16}/> : <FileText size={16}/>; }
function FileModal({ title, onClose, onSubmit }) { const [area,setArea]=useState('source');const[path,setPath]=useState('');const[content,setContent]=useState('');return <Modal title={title} onClose={onClose}><div className="two"><label>Area<select value={area} onChange={e=>setArea(e.target.value)}><option>source</option><option>workspace</option></select></label><label>Path<input value={path} onChange={e=>setPath(e.target.value)} placeholder="folder/file.txt"/></label></div><label>Initial content<textarea value={content} onChange={e=>setContent(e.target.value)} rows={12}/></label><div className="modal-actions"><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!path.trim()} onClick={()=>onSubmit({area,path,content})}>Create</Button></div></Modal>; }
function UploadModal({ defaultArea, onClose, onSubmit }) {
  const [area, setArea] = useState(defaultArea);
  const [folder, setFolder] = useState('');
  const [overwrite, setOverwrite] = useState(false);
  const [picked, setPicked] = useState([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);
  const add = list => setPicked(cur => {
    const have = new Set(cur.map(f => `${f.name}:${f.size}`));
    return [...cur, ...Array.from(list).filter(f => !have.has(`${f.name}:${f.size}`))];
  });
  const submit = async () => { setBusy(true); try { await onSubmit({ area, folder, overwrite, picked }); } finally { setBusy(false); } };
  return <Modal title="Upload files" onClose={onClose}>
    <div className="two">
      <label>Area<select value={area} onChange={e => setArea(e.target.value)}><option>source</option><option>workspace</option></select></label>
      <label>Folder (optional)<input value={folder} onChange={e => setFolder(e.target.value)} placeholder="docs/specs"/></label>
    </div>
    <div className={`dropzone ${dragging ? 'active' : ''}`} role="button" tabIndex={0}
      onClick={() => inputRef.current?.click()} onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && inputRef.current?.click()}
      onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)}
      onDrop={e => { e.preventDefault(); setDragging(false); add(e.dataTransfer.files); }}>
      <Upload size={22}/><strong>Drop files here or click to browse</strong><span className="muted">Any file type. Several files upload as one commit.</span>
      <input ref={inputRef} type="file" multiple hidden onChange={e => { add(e.target.files); e.target.value = ''; }}/>
    </div>
    {picked.length > 0 && <div className="upload-list">{picked.map(f => <div key={`${f.name}:${f.size}`}><span title={f.name}>{f.name}</span><small>{fmtBytes(f.size)}</small><button className="icon-btn" aria-label={`Remove ${f.name}`} onClick={() => setPicked(cur => cur.filter(x => x !== f))}><X size={14}/></button></div>)}</div>}
    <label className="check-line"><input type="checkbox" checked={overwrite} onChange={e => setOverwrite(e.target.checked)}/>Overwrite files that already exist</label>
    <div className="modal-actions"><Button onClick={onClose}>Cancel</Button><Button variant="primary" icon={Upload} disabled={busy || !picked.length} onClick={submit}>{busy ? 'Uploading…' : `Upload ${picked.length || ''} file${picked.length === 1 ? '' : 's'}`.trim()}</Button></div>
  </Modal>;
}
function MoveModal({ file, copy, onClose, onSubmit }) { const [area,setArea]=useState(file.area);const[path,setPath]=useState(file.path);return <Modal title={copy?'Copy file':'Move file'} onClose={onClose}><p className="muted">{file.area}/{file.path}</p><div className="two"><label>Destination area<select value={area} onChange={e=>setArea(e.target.value)}><option>source</option><option>workspace</option></select></label><label>Destination path<input value={path} onChange={e=>setPath(e.target.value)}/></label></div><div className="modal-actions"><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={()=>onSubmit({source_area:file.area,source_path:file.path,destination_area:area,destination_path:path,message:`${copy?'Copy':'Move'} ${file.path}`})}>{copy?'Copy':'Move'}</Button></div></Modal>; }
function HistoryModal({ file, history, onClose, onRevision }) { return <Modal title={`History · ${file.path}`} wide onClose={onClose}><div className="history-list">{safeArray(history).map((h,i)=><div className="history-item" key={i}><div><strong>{h.commit || h.id || `Revision ${i+1}`}</strong><small>{h.message || h.description || ''}</small></div>{h.commit && <Button onClick={()=>onRevision(h.commit)}>Load</Button>}</div>)}{!history.length&&<div className="muted">No history available.</div>}</div></Modal>; }
function CognitionView({ username, project, notify, onError }) { const [o,setO]=useState(null);const[chosen,setChosen]=useState([]);const[compiling,setCompiling]=useState(false);const[progress,setProgress]=useState(null);const[baseline,setBaseline]=useState(null);const[element,setElement]=useState('');const[impact,setImpact]=useState(null);const[result,setResult]=useState(null);useEffect(()=>{if(!result)return;const t=setTimeout(()=>setResult(null),6000);return()=>clearTimeout(t)},[result]);const load=async()=>{try{setO(await api.cognitionOverview(username,project))}catch(e){onError(e)}};useEffect(()=>{load()},[username,project]);const files=o?.available_files||[];const metrics=[['entities','Entities'],['relationships','Relationships'],['events','Events'],['locations','Locations'],['concepts','Concepts'],['definitions','Definitions'],['knowledge','Knowledge']];const compile=async()=>{setCompiling(true);setResult(null);setProgress({done:0,total:0,text:'Preparing selected files…'});setBaseline(o?.counts||{});const merge=r=>setO(x=>({...x,counts:r.counts??x?.counts,state:r.state??x?.state}));try{await api.compileStream(username,project,chosen,{onStart:r=>{merge(r);setBaseline(r.counts||{});setProgress({done:0,total:0,text:'Compilation started — preparing source…'})},onProgress:r=>{merge(r);setProgress({done:r.done,total:r.total,text:r.total?`Compiling batch ${r.done}/${r.total}`:'Compiling…'})},onComplete:r=>{merge(r);setProgress({done:1,total:1,text:'Compilation complete'});setResult({kind:'success',text:`Processed ${r.result?.source_files??0} source + ${r.result?.workspace_files??0} workspace file(s).`});load()},onError:r=>{setResult({kind:'error',text:typeof r?.detail==='string'?r.detail:JSON.stringify(r?.detail??r)})}})}catch(e){setResult({kind:'error',text:e.message||String(e)})}finally{setCompiling(false);setTimeout(()=>setProgress(null),2500)}};return <div className="page"><div className="page-head"><div><h2>Persistent cognition</h2><p>Project-scoped world model compiled from Source and Workspace artifacts.</p></div><Button icon={RefreshCw} onClick={load}>Refresh</Button></div><div className="metric-grid">{metrics.map(([k,l])=><div className="metric" key={k}><span>{l}</span><strong>{o?.counts?.[k] ?? 0}{baseline&&(o?.counts?.[k]??0)-(baseline[k]??0)>0&&<small style={{marginLeft:8,fontSize:13,color:'var(--ok)'}}>+{(o?.counts?.[k]??0)-(baseline[k]??0)}</small>}</strong></div>)}<div className="metric"><span>Version</span><strong>{o?.state?.current_version ?? 0}</strong></div></div>{progress&&<div className="alert info" role="status"><div style={{display:'flex',justifyContent:'space-between',marginBottom:8}}><span>{progress.text}</span>{progress.total>0&&<span>{Math.round(progress.done/progress.total*100)}%</span>}</div><div style={{height:6,borderRadius:4,background:'var(--line-2)',overflow:'hidden'}}><div style={{height:'100%',width:`${progress.total?Math.min(100,progress.done/progress.total*100):(compiling?8:100)}%`,background:'var(--accent)',transition:'width .3s ease'}}/></div></div>}{result?<div className={`alert ${result.kind}`} role="status">{result.text}</div>:!o?.state?.compiled&&<div className="alert info">Cognition is ready but has not been compiled from project artifacts. This does not block chat or file work.</div>}<section className="card"><div className="card-head"><div><h3>Compile selected files</h3><p>Select the artifacts that should participate in this cognition build.</p></div><Button variant="primary" icon={Play} disabled={!chosen.length||compiling} onClick={compile}>{compiling?'Compiling…':'Compile selected files'}</Button></div><div className="check-grid">{files.map(f=>{const key=`${f.area}|${f.path}`;return <label className="check-item" key={key}><input type="checkbox" checked={chosen.includes(key)} onChange={e=>setChosen(v=>e.target.checked?[...v,key]:v.filter(x=>x!==key))}/><span><b>[{f.area}] {f.path}</b><small>{f.size || ''}</small></span></label>})}</div></section><JsonDisclosure title="Ledger" value={o?.ledger}/><JsonDisclosure title="Relationships" value={o?.relationships}/><JsonDisclosure title="State map" value={o?.state}/><section className="card"><div className="card-head"><div><h3>Impact analysis</h3><p>Inspect how a cognition element affects the project.</p></div><div className="impact-row"><input value={element} onChange={e=>setElement(e.target.value)} placeholder="Entity / element"/><Button onClick={async()=>{try{setImpact(await api.impact(username,project,element))}catch(e){onError(e)}}}>Analyze</Button></div></div>{impact&&<JsonBlock value={impact}/>}</section></div>; }
function JsonDisclosure({title,value}) { const[open,setOpen]=useState(false);return <div className="disclosure"><button onClick={()=>setOpen(!open)}>{open?<ChevronDown/>:<ChevronRight/>}<span>{title}</span></button>{open&&<JsonBlock value={value ?? {}}/>}</div>; }

function ReviewView({ username, project, notify, onError }) { const[drafts,setDrafts]=useState([]);const[open,setOpen]=useState({});const[reason,setReason]=useState({});const load=async()=>{try{setDrafts(await api.drafts(username,project))}catch(e){onError(e)}};useEffect(()=>{load()},[username,project]);const save=async d=>{try{await api.saveDraft(username,project,d.id,{content:d.content,metadata:d.metadata||{}});notify('Draft saved','success');load()}catch(e){onError(e)}};return <div className="page"><div className="page-head"><div><h2>Draft review</h2><p>AI output is persisted in the workspace. Approval is the only path that writes an agent-generated change into /source.</p></div><Button icon={RefreshCw} onClick={load}>Refresh</Button></div>{!drafts.length?<EmptyState icon={ClipboardList} title="No drafts yet" text="Agent-generated drafts will appear here when a task creates one."/>:<div className="stack">{drafts.map(d=>{const meta=d.metadata||{};return <section className="draft card" key={d.id}><button className="draft-head" onClick={()=>setOpen(v=>({...v,[d.id]:!v[d.id]}))}><span className={`status ${d.status}`}>{String(d.status||'pending').toUpperCase()}</span><strong>{shortId(d.id)}</strong><span>{meta.target_file || 'conversation response'}</span>{open[d.id]?<ChevronDown/>:<ChevronRight/>}</button>{(open[d.id]||d.status==='pending')&&<div className="draft-body"><label>Draft content<textarea value={d.content||''} onChange={e=>setDrafts(v=>v.map(x=>x.id===d.id?{...x,content:e.target.value}:x))} rows={14}/></label><div className="two"><label>Target file<input value={meta.target_file||''} onChange={e=>setDrafts(v=>v.map(x=>x.id===d.id?{...x,metadata:{...meta,target_file:e.target.value}}:x))}/></label><label>Change description<input value={meta.change_description||''} onChange={e=>setDrafts(v=>v.map(x=>x.id===d.id?{...x,metadata:{...meta,change_description:e.target.value}}:x))}/></label></div>{d.status==='pending'&&<div className="row-actions"><Button icon={Save} onClick={()=>save(d)}>Save edits</Button><Button variant="primary" icon={ShieldCheck} onClick={async()=>{try{await api.approveDraft(username,project,d.id);notify('Approved and committed to source','success');load()}catch(e){onError(e)}}}>Approve</Button><Button icon={X} onClick={()=>setReason(v=>({...v,[d.id]:v[d.id]!==undefined?undefined:''}))}>Reject</Button>{reason[d.id]!==undefined&&<div className="reject-box"><textarea value={reason[d.id]} onChange={e=>setReason(v=>({...v,[d.id]:e.target.value}))} placeholder="Why was this rejected?"/><Button variant="danger" onClick={async()=>{try{await api.rejectDraft(username,project,d.id,reason[d.id]);notify('Draft rejected','success');load()}catch(e){onError(e)}}}>Confirm rejection</Button></div>}</div>}</div>}</section>})}</div>}</div>; }

function InstructionsView({ username, project, notify, onError }) { const[items,setItems]=useState([]);const[add,setAdd]=useState(false);const[form,setForm]=useState({content:'',scope:'situational',tagged_entity_id:''});const load=async()=>{try{setItems(await api.instructions(username,project))}catch(e){onError(e)}};useEffect(()=>{load()},[username,project]);const addOne=async()=>{if(!form.content.trim())return;try{await api.addInstruction(username,project,form);setForm({content:'',scope:'situational',tagged_entity_id:''});setAdd(false);notify('Instruction added','success');load()}catch(e){onError(e)}};return <div className="page"><div className="page-head"><div><h2>Persistent instructions</h2><p>Standing directives and rejection-derived constraints survive chat-session clearing.</p></div><Button variant="primary" icon={Plus} onClick={()=>setAdd(!add)}>Add instruction</Button></div>{add&&<section className="card"><label>Instruction<textarea value={form.content} onChange={e=>setForm({...form,content:e.target.value})}/></label><div className="two"><label>Scope<select value={form.scope} onChange={e=>setForm({...form,scope:e.target.value})}><option>system</option><option>file_tagged</option><option>situational</option></select></label>{form.scope==='file_tagged'&&<label>Tagged entity ID<input value={form.tagged_entity_id} onChange={e=>setForm({...form,tagged_entity_id:e.target.value})}/></label>}</div><div className="modal-actions"><Button onClick={()=>setAdd(false)}>Cancel</Button><Button variant="primary" onClick={addOne}>Add instruction</Button></div></section>}<div className="stack">{items.map(i=><InstructionCard key={i.id} item={i} username={username} project={project} notify={notify} onError={onError} onReload={load}/>)}</div></div>; }
function InstructionCard({item,username,project,notify,onError,onReload}) { const[content,setContent]=useState(item.content||'');return <section className="card instruction"><div className="instruction-meta"><span className={`status ${item.status}`}>{item.status}</span><span>{item.scope}</span><code>{shortId(item.id)}</code></div><textarea value={content} onChange={e=>setContent(e.target.value)}/>{item.status==='active'&&<div className="row-actions"><Button icon={Save} onClick={async()=>{try{await api.updateInstruction(username,project,item.id,content);notify('Instruction updated','success');onReload()}catch(e){onError(e)}}}>Save</Button><Button icon={X} onClick={async()=>{try{await api.deactivateInstruction(username,project,item.id);notify('Instruction deactivated','success');onReload()}catch(e){onError(e)}}}>Deactivate</Button></div>}</section>; }

function HistoryView({ username, project, notify, onError }) { const[log,setLog]=useState('');const load=async()=>{try{setLog((await api.history(username,project)).log||'')}catch(e){onError(e)}};useEffect(()=>{load()},[username,project]);return <div className="page"><div className="page-head"><div><h2>Git history</h2><p>Past states can be inspected through commit hashes; reversion is deliberately separate from ordinary draft approval.</p></div><div className="row-actions"><Button icon={RefreshCw} onClick={load}>Refresh</Button><Button icon={Archive} onClick={async()=>{try{await api.checkpoint(username,project,'Manual project checkpoint');notify('Checkpoint created','success');load()}catch(e){onError(e)}}}>Checkpoint</Button></div></div><div className="terminal"><pre>{log || 'No commits yet.'}</pre></div></div>; }

function SettingsView({ username, project, notify, onError }) { const[data,setData]=useState(null);const[form,setForm]=useState(null);const load=async()=>{try{const r=await api.settings(username,project);setData(r);const c=r.config||{};setForm({provider:{...(c.provider||{})},limits:{...(c.limits||{}),...(r.limits||{})},features:{...(c.features||{})},api_key:''})}catch(e){onError(e)}};useEffect(()=>{load()},[username,project]);if(!form)return <div className="page"><div className="loading"><span className="spinner"/>Loading settings…</div></div>;const specs=data?.provider_specs||{};const p=form.provider.name||Object.keys(specs)[0]||'';const spec=specs[p]||{};return <div className="page"><div className="page-head"><div><h2>Project settings</h2><p>Provider, agent limits, feature flags and API credentials for this project.</p></div><Button variant="primary" icon={Save} onClick={async()=>{try{await api.updateSettings(username,project,form);notify('Settings saved to this project','success');load()}catch(e){onError(e)}}}>Save settings</Button></div><section className="card"><h3>LLM provider</h3><div className="two"><label>Provider<select value={p} onChange={e=>{const n=e.target.value;setForm(v=>({...v,provider:{...v.provider,name:n,model:specs[n]?.default_model||''}}))}}>{Object.entries(specs).map(([id,s])=><option key={id} value={id}>{s.label||id}</option>)}</select></label><label>{spec.model_label||'Model'}<input value={form.provider.model||''} onChange={e=>setForm(v=>({...v,provider:{...v.provider,model:e.target.value}}))}/></label></div>{spec.endpoint_env&&<label>Azure Foundry endpoint<input value={form.provider.endpoint||''} onChange={e=>setForm(v=>({...v,provider:{...v.provider,endpoint:e.target.value}}))} placeholder="https://<resource>.services.ai.azure.com"/></label>}<label>{spec.key_label||'API key'}<input type="password" value={form.api_key} onChange={e=>setForm(v=>({...v,api_key:e.target.value}))} placeholder="Leave blank to keep existing key"/></label><label>Fallback providers<select multiple value={form.provider.fallback||[]} onChange={e=>setForm(v=>({...v,provider:{...v.provider,fallback:[...e.target.selectedOptions].map(o=>o.value)}}))}>{Object.entries(specs).filter(([id])=>id!==p).map(([id,s])=><option key={id} value={id}>{s.label||id}</option>)}</select></label></section><section className="card"><h3>Limits</h3><div className="four">{[['max_agent_steps','Max agent steps',1,100],['transcript_turns','Transcript clearing turn threshold',5,1000],['transcript_chars','Transcript clearing character threshold',1000,1000000],['requests_per_minute','Provider requests/minute',1,1000]].map(([k,l,min,max])=><label key={k}>{l}<input type="number" min={min} max={max} value={form.limits[k]??''} onChange={e=>setForm(v=>({...v,limits:{...v.limits,[k]:Number(e.target.value)}}))}/></label>)}</div></section><section className="card"><h3>Features</h3><Toggle value={!!form.features.semantic_propagation} onChange={v=>setForm(x=>({...x,features:{...x.features,semantic_propagation:v}}))} label="Semantic propagation"/></section></div>; }