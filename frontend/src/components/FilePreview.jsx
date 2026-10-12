import React, { useEffect, useRef, useState } from 'react';
import { Download, ExternalLink } from 'lucide-react';
import { api, fetchBlob } from '../api';
import Office from './Office';
import Markdown from './Markdown';

const OFFICE = new Set(['.docx', '.pptx', '.xlsx', '.xls']);
const IMAGES = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.svg']);
const LANG = { '.py': 'python', '.js': 'javascript', '.jsx': 'jsx', '.ts': 'typescript', '.tsx': 'tsx', '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml', '.css': 'css', '.xml': 'xml', '.sql': 'sql', '.java': 'java', '.cs': 'csharp', '.sh': 'bash', '.toml': 'toml' };
const MAX_TEXT = 1_000_000;
// Any file without a dedicated viewer is shown as text. NUL bytes become a visible symbol so binary
// content stays readable-ish instead of being refused.
async function readText(blob) {
  const text = (await blob.slice(0, MAX_TEXT).text()).replace(/\0/g, '\u2400');
  return { kind: 'text', content: text, truncated: blob.size > MAX_TEXT };
}
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

export default function FilePreview({ username, project, area, path, text }) {
  const ext = extOf(path);
  const raw = api.rawUrl(username, project, area, path);
  const download = api.downloadUrl(username, project, area, path);
  const openInNewTab = async () => {
    const tab = window.open('', '_blank');
    if (!tab) { window.alert('Allow pop-ups to open the diagram in a new tab.'); return; }
    tab.document.title = path;
    tab.document.body.innerHTML = '<p style="font:16px sans-serif;padding:24px">Opening diagram…</p>';
    try {
      if (ext === '.mmd') {
        const sourceText = text != null ? text : (st.data?.content ?? await (await fetchBlob(username, project, area, path)).text());
        const src = sourceText.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').trim().replace(/^\s*```(?:mermaid)?\s*\n/i, '').replace(/\n\s*```\s*$/, '').trim();
        const { default: mermaid } = await import('mermaid');
        mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'default' });
        const { svg } = await mermaid.render(`open-${Date.now()}`, src);
        tab.document.open();
        tab.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${path.replace(/[<>]/g, '')}</title><style>body{margin:0;padding:24px;background:#fff}svg{max-width:100%;height:auto}</style></head><body>${svg}</body></html>`);
        tab.document.close();
      } else {
        tab.location.href = raw;
      }
    } catch (e) { tab.document.body.textContent = `Could not open diagram: ${e?.message || e}`; }
  };
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
        } else if (text != null) {
          if (live) setSt({ loading: false, data: { kind: 'text', content: text }, blob: null, url: '', htmlSrc: '', error: '' });
        } else if (isHtml) {
          // Preferred: the sandboxed /raw route (relative assets resolve). Fallback: inline the text.
          const probe = await fetch(raw).catch(() => null);
          if (!live) return;
          if (probe?.ok) setSt({ loading: false, data: null, blob: null, url: '', htmlSrc: raw, error: '' });
          else { const d = await readText(await fetchBlob(username, project, area, path)); if (live) setSt({ loading: false, data: d, blob: null, url: '', htmlSrc: '', error: '' }); }
        } else {
          const d = await readText(await fetchBlob(username, project, area, path));
          if (live) setSt({ loading: false, data: d, blob: null, url: '', htmlSrc: '', error: '' });
        }
      } catch (e) { if (live) setSt({ loading: false, data: null, blob: null, url: '', htmlSrc: '', error: e.message }); }
    })();
    return () => { live = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [username, project, area, path, text]);

  const bar = <div className="preview-bar"><span title={`${area}/${path}`}>{area}/{path}</span>
    {(ext === '.mmd' || ext === '.svg' || ext === '.png' || ext === '.jpg' || ext === '.jpeg' || ext === '.webp') && <button type="button" onClick={openInNewTab} data-tip="Open in new tab" aria-label="Open in new tab"><ExternalLink size={14}/></button>}
    <a href={download} data-tip="Download" data-tip-align="end" aria-label="Download"><Download size={14}/></a></div>;

  const note = st.data?.truncated && <p className="muted">Showing the first 1 MB. Download for the full file.</p>;
  let body;
  if (st.loading) body = <div className="loading"><span className="spinner"/>Loading…</div>;
  else if (st.error) body = <pre className="preview-error">{st.error}</pre>;
  else if (OFFICE.has(ext)) body = <Office blob={st.blob} ext={ext} />;
  else if (ext === '.pdf') body = <iframe className="preview-frame" src={st.url} title={path} />;
  else if (IMAGES.has(ext)) body = <div className="preview-image"><img src={st.url} alt={path} /></div>;
  else if (isHtml && st.htmlSrc) body = <iframe key={st.htmlSrc} className="preview-frame" src={st.htmlSrc} title={path} sandbox="allow-scripts allow-popups allow-forms" />;
  else if (isHtml && st.data?.kind === 'text') body = <iframe className="preview-frame" srcDoc={st.data.content} title={path} sandbox="allow-scripts allow-popups allow-forms" />;
  else if (ext === '.md') body = st.data?.content != null ? <div className="preview-doc"><Markdown>{st.data.content}</Markdown>{note}</div> : <pre className="preview-error">No file content was returned.</pre>;
  else if (ext === '.mmd') body = st.data?.content != null ? <div className="preview-doc"><Mermaid source={st.data.content} /></div> : <pre className="preview-error">No diagram source was returned.</pre>;
  else body = st.data?.content != null ? <div className="preview-doc"><Markdown>{fence(st.data.content, LANG[ext] || 'text')}</Markdown>{note}</div> : <pre className="preview-error">No file content was returned.</pre>;

  return <div className="file-preview">{bar}<div className="preview-body">{body}</div></div>;
}