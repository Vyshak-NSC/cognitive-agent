import React, { useEffect, useRef, useState } from 'react';
import { Download, FileText } from 'lucide-react';
import { api, fetchBlob } from '../api';
import Office from './Office';
import Markdown from './Markdown';

const OFFICE = new Set(['.docx', '.pptx', '.xlsx', '.xls']);
const IMAGES = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.svg']);
const LANG = { '.py': 'python', '.js': 'javascript', '.jsx': 'jsx', '.ts': 'typescript', '.tsx': 'tsx', '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml', '.css': 'css', '.xml': 'xml', '.sql': 'sql', '.java': 'java', '.cs': 'csharp', '.sh': 'bash', '.toml': 'toml' };
const extOf = p => { const i = p.lastIndexOf('.'); return i < 0 ? '' : p.slice(i).toLowerCase(); };
// Fence longer than any backtick run inside the content, so file content can't break out.
const fence = (text, lang) => { const n = Math.max(3, ...(text.match(/`+/g) || []).map(s => s.length + 1)); const f = '`'.repeat(n); return `${f}${lang}\n${text}\n${f}`; };

function Mermaid({ source }) {
  const ref = useRef(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const { default: mermaid } = await import('mermaid');
        mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'dark' });
        const src = source.trim().replace(/^```\w*\n/, '').replace(/\n```$/, '');
        const { svg } = await mermaid.render(`mmd-${Math.random().toString(36).slice(2)}`, src);
        if (live && ref.current) ref.current.innerHTML = svg;
      } catch (e) { if (live) setErr(e?.message || String(e)); }
    })();
    return () => { live = false; };
  }, [source]);
  return err ? <pre className="preview-error">Mermaid render error: {err}</pre> : <div className="mermaid-view" ref={ref} />;
}

export default function FilePreview({ username, project, area, path }) {
  const ext = extOf(path);
  const raw = api.rawUrl(username, project, area, path);
  const download = api.downloadUrl(username, project, area, path);
  const isHtml = ext === '.html' || ext === '.htm';
  const isBlob = OFFICE.has(ext) || IMAGES.has(ext) || ext === '.pdf';
  const [st, setSt] = useState({ loading: true, data: null, blob: null, url: '', htmlSrc: '', error: '' });

  useEffect(() => {
    let live = true, objectUrl = '';
    setSt({ loading: true, data: null, blob: null, url: '', htmlSrc: '', error: '' });
    (async () => {
      try {
        if (isBlob) {
          const blob = await fetchBlob(username, project, area, path);
          if (!live) return;
          objectUrl = OFFICE.has(ext) ? '' : URL.createObjectURL(blob);
          setSt({ loading: false, data: null, blob, url: objectUrl, htmlSrc: '', error: '' });
        } else if (isHtml) {
          // Preferred: the sandboxed /raw route (relative assets resolve). Fallback: inline the text.
          const probe = await fetch(raw).catch(() => null);
          if (!live) return;
          if (probe?.ok) setSt({ loading: false, data: null, blob: null, url: '', htmlSrc: raw, error: '' });
          else { const d = await api.filePreview(username, project, area, path); if (live) setSt({ loading: false, data: d, blob: null, url: '', htmlSrc: '', error: '' }); }
        } else {
          const d = await api.filePreview(username, project, area, path);
          if (live) setSt({ loading: false, data: d, blob: null, url: '', htmlSrc: '', error: '' });
        }
      } catch (e) { if (live) setSt({ loading: false, data: null, blob: null, url: '', htmlSrc: '', error: e.message }); }
    })();
    return () => { live = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [username, project, area, path]);

  const bar = <div className="preview-bar"><span title={`${area}/${path}`}>{area}/{path}</span>
    <a href={download} title="Download"><Download size={14}/></a></div>;

  let body;
  if (st.loading) body = <div className="loading"><span className="spinner"/>Loading…</div>;
  else if (st.error) body = <pre className="preview-error">{st.error}</pre>;
  else if (OFFICE.has(ext)) body = <Office blob={st.blob} ext={ext} />;
  else if (ext === '.pdf') body = <iframe className="preview-frame" src={st.url} title={path} />;
  else if (IMAGES.has(ext)) body = <div className="preview-image"><img src={st.url} alt={path} /></div>;
  else if (isHtml && st.htmlSrc) body = <iframe key={st.htmlSrc} className="preview-frame" src={st.htmlSrc} title={path} sandbox="allow-scripts allow-popups allow-forms" />;
  else if (isHtml && st.data?.kind === 'text') body = <iframe className="preview-frame" srcDoc={st.data.content} title={path} sandbox="allow-scripts allow-popups allow-forms" />;
  else if (st.data?.kind !== 'text') body = <div className="binary-preview"><FileText size={32}/><p>No inline preview for this file type.</p><a className="btn primary" href={download}><Download size={15}/>Download</a></div>;
  else if (ext === '.md') body = <div className="preview-doc"><Markdown>{st.data.content}</Markdown></div>;
  else if (ext === '.mmd') body = <div className="preview-doc"><Mermaid source={st.data.content} /></div>;
  else body = <div className="preview-doc"><Markdown>{fence(st.data.content, LANG[ext] || 'text')}</Markdown></div>;

  return <div className="file-preview">{bar}<div className="preview-body">{body}</div></div>;
}
