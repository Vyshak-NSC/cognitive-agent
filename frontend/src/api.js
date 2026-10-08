const BASE = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

export function apiUrl(path) { return `${BASE}${path}`; }

async function request(path, options = {}) {
  const res = await fetch(apiUrl(path), {
    ...options,
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) },
  });
  const type = res.headers.get('content-type') || '';
  const payload = type.includes('application/json') ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = typeof payload === 'object' ? payload?.detail : payload;
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }
  return payload;
}

const enc = encodeURIComponent;
const seg = p => String(p).split('/').map(enc).join('/');
const get = path => request(path);
const post = (path, body) => request(path, { method: 'POST', body: JSON.stringify(body) });
const put = (path, body) => request(path, { method: 'PUT', body: JSON.stringify(body) });
const patch = (path, body) => request(path, { method: 'PATCH', body: body === undefined ? undefined : JSON.stringify(body) });
const del = path => request(path, { method: 'DELETE' });

export const api = {
  base: BASE,
  // Streams the file with its real MIME type. Relative URLs inside an HTML file resolve against
  // this path, so sibling CSS/JS/images load without any server-side inlining.
  rawUrl: (u,p,a,path) => apiUrl(`/projects/${enc(u)}/${enc(p)}/files/${enc(a)}/raw/${seg(path)}`),
  downloadUrl: (u,p,a,path) => apiUrl(`/projects/${enc(u)}/${enc(p)}/files/${enc(a)}/download?path=${enc(path)}`),
  health: () => get('/health'),
  providers: () => get('/providers'),
  projects: username => get(`/projects/${encodeURIComponent(username)}`),
  projectDetails: username => get(`/projects/${encodeURIComponent(username)}/details`),
  project: (u,p) => get(`/projects/${encodeURIComponent(u)}/${encodeURIComponent(p)}`),
  createProject: (username, project_id) => post('/projects', { username, project_id }),
  renameProject: (username, project_id, new_name) => put(`/projects/${encodeURIComponent(username)}/${encodeURIComponent(project_id)}`, { username, project_id, new_name }),
  deleteProject: (username, project_id) => del(`/projects/${encodeURIComponent(username)}/${encodeURIComponent(project_id)}`),

  sessions: (u,p) => get(`/sessions/${encodeURIComponent(u)}/${encodeURIComponent(p)}`),
  session: (u,p,s) => get(`/sessions/${encodeURIComponent(u)}/${encodeURIComponent(p)}/${encodeURIComponent(s)}`),
  createSession: (u,p,title) => post(`/sessions/${encodeURIComponent(u)}/${encodeURIComponent(p)}`, title ? { title } : {}),
  renameSession: (u,p,s,title) => patch(`/sessions/${encodeURIComponent(u)}/${encodeURIComponent(p)}/${encodeURIComponent(s)}`, { title }),
  deleteSession: (u,p,s) => del(`/sessions/${encodeURIComponent(u)}/${encodeURIComponent(p)}/${encodeURIComponent(s)}`),
  sessionMemory: (u,p,s) => get(`/sessions/${encodeURIComponent(u)}/${encodeURIComponent(p)}/${encodeURIComponent(s)}/memory`),
  activateTurn: (u,p,s,turn_id) => post(`/sessions/${encodeURIComponent(u)}/${encodeURIComponent(p)}/${encodeURIComponent(s)}/activate`, { turn_id }),
  editTurn: (u,p,s,turn_id,content) => post(`/sessions/${encodeURIComponent(u)}/${encodeURIComponent(p)}/${encodeURIComponent(s)}/edit`, { turn_id, content }),
  chatTurn: (body) => post('/query/turn', body),

  drafts: (u,p) => get(`/drafts/${encodeURIComponent(u)}/${encodeURIComponent(p)}`),
  draft: (u,p,d) => get(`/drafts/${encodeURIComponent(u)}/${encodeURIComponent(p)}/${encodeURIComponent(d)}`),
  saveDraft: (u,p,d,body) => put(`/drafts/${encodeURIComponent(u)}/${encodeURIComponent(p)}/${encodeURIComponent(d)}`, body),
  approveDraft: (u,p,d) => post(`/drafts/${encodeURIComponent(u)}/${encodeURIComponent(p)}/${encodeURIComponent(d)}/approve`, {}),
  rejectDraft: (u,p,d,reason) => post(`/drafts/${encodeURIComponent(u)}/${encodeURIComponent(p)}/${encodeURIComponent(d)}/reject`, { reason }),

  instructions: (u,p) => get(`/instructions/${encodeURIComponent(u)}/${encodeURIComponent(p)}`),
  addInstruction: (u,p,body) => post(`/instructions/${encodeURIComponent(u)}/${encodeURIComponent(p)}`, body),
  updateInstruction: (u,p,id,content) => patch(`/instructions/${encodeURIComponent(u)}/${encodeURIComponent(p)}/${encodeURIComponent(id)}`, { content }),
  deactivateInstruction: (u,p,id) => patch(`/instructions/${encodeURIComponent(u)}/${encodeURIComponent(p)}/${encodeURIComponent(id)}`),

  cognitionOverview: (u,p) => get(`/cognition/${encodeURIComponent(u)}/${encodeURIComponent(p)}/overview`),
  cognitionFiles: (u,p) => get(`/cognition/${encodeURIComponent(u)}/${encodeURIComponent(p)}/files`),
  compile: (u,p,files) => post(`/cognition/${encodeURIComponent(u)}/${encodeURIComponent(p)}/compile`, { files }),
  validate: (u,p) => get(`/cognition/${encodeURIComponent(u)}/${encodeURIComponent(p)}/validate`),
  impact: (u,p,element) => get(`/cognition/${encodeURIComponent(u)}/${encodeURIComponent(p)}/impact?element=${encodeURIComponent(element)}`),

  files: (u,p,area) => get(`/projects/${encodeURIComponent(u)}/${encodeURIComponent(p)}/files${area ? `?area=${encodeURIComponent(area)}` : ''}`),
  fileText: (u,p,a,path) => get(`/projects/${encodeURIComponent(u)}/${encodeURIComponent(p)}/files/${encodeURIComponent(a)}/text?path=${encodeURIComponent(path)}`),
  filePreview: (u,p,a,path) => get(`/projects/${encodeURIComponent(u)}/${encodeURIComponent(p)}/files/${encodeURIComponent(a)}/preview?path=${encodeURIComponent(path)}`),
  fileHistory: (u,p,a,path,limit=50) => get(`/projects/${encodeURIComponent(u)}/${encodeURIComponent(p)}/files/${encodeURIComponent(a)}/history?path=${encodeURIComponent(path)}&limit=${limit}`),
  fileRevision: (u,p,a,path,commit) => get(`/projects/${encodeURIComponent(u)}/${encodeURIComponent(p)}/files/${encodeURIComponent(a)}/revision?path=${encodeURIComponent(path)}&commit=${encodeURIComponent(commit)}`),
  fileDiff: (u,p,a,path,aRev,bRev) => get(`/projects/${encodeURIComponent(u)}/${encodeURIComponent(p)}/files/${encodeURIComponent(a)}/diff?path=${encodeURIComponent(path)}&revision_a=${encodeURIComponent(aRev)}&revision_b=${encodeURIComponent(bRev)}`),
  writeFile: (u,p,a,path,body) => put(`/projects/${encodeURIComponent(u)}/${encodeURIComponent(p)}/files/${encodeURIComponent(a)}/text?path=${encodeURIComponent(path)}`, body),
  deleteFile: (u,p,a,path) => del(`/projects/${encodeURIComponent(u)}/${encodeURIComponent(p)}/files/${encodeURIComponent(a)}?path=${encodeURIComponent(path)}`),
  moveFile: (u,p,body) => post(`/projects/${encodeURIComponent(u)}/${encodeURIComponent(p)}/files/move`, body),
  copyFile: (u,p,body) => post(`/projects/${encodeURIComponent(u)}/${encodeURIComponent(p)}/files/copy`, body),
  restoreFile: (u,p,a,path,body) => post(`/projects/${encodeURIComponent(u)}/${encodeURIComponent(p)}/files/${encodeURIComponent(a)}/restore?path=${encodeURIComponent(path)}`, body),
  checkpoint: (u,p,message) => post(`/projects/${encodeURIComponent(u)}/${encodeURIComponent(p)}/vcs/checkpoint`, { message }),
  history: (u,p) => get(`/git/${encodeURIComponent(u)}/${encodeURIComponent(p)}/log`),

  agents: (u,p) => get(`/agents/${encodeURIComponent(u)}/${encodeURIComponent(p)}`),
  agent: (u,p,id) => get(`/agents/${encodeURIComponent(u)}/${encodeURIComponent(p)}/${encodeURIComponent(id)}`),
  saveAgent: (u,p,body) => put(`/agents/${encodeURIComponent(u)}/${encodeURIComponent(p)}`, body),
  deleteAgent: (u,p,id) => del(`/agents/${encodeURIComponent(u)}/${encodeURIComponent(p)}/${encodeURIComponent(id)}`),

  settings: (u,p) => get(`/settings/${encodeURIComponent(u)}/${encodeURIComponent(p)}`),
  updateSettings: (u,p,body) => put(`/settings/${encodeURIComponent(u)}/${encodeURIComponent(p)}`, body),
};

// Binary fetch through the existing /download endpoint (no backend change required).
export async function fetchBlob(u, p, a, path) {
  const res = await fetch(api.downloadUrl(u, p, a, path));
  if (!res.ok) {
    let detail = ''; try { detail = (await res.json())?.detail; } catch { /* non-JSON body */ }
    throw new Error(detail || `Could not load ${path} (${res.status})`);
  }
  return res.blob();
}

export async function streamChat(body, handlers = {}, signal) {
  const res = await fetch(apiUrl('/query/stream'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || `${res.status} ${res.statusText}`);
  }
  if (!res.body) return null;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
    const chunks = buffer.split('\n\n');
    buffer = chunks.pop() || '';
    for (const chunk of chunks) {
      let event = 'message';
      let data = '';
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        if (line.startsWith('data:')) data += (data ? '\n' : '') + line.slice(5).replace(/^ /, '');
      }
      if (!data) continue;
      let parsed;
      try { parsed = JSON.parse(data); } catch { parsed = data; }
      if (event === 'section') handlers.onSection?.(parsed);
      else if (event === 'plan') handlers.onPlan?.(parsed);
      else if (event === 'complete') handlers.onComplete?.(parsed);
      else if (event === 'error') handlers.onError?.(parsed);
    }
  }
}
