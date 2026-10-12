import React, { memo, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize from 'rehype-sanitize';
import rehypeHighlight from 'rehype-highlight';
import { Check, Copy, ExternalLink, Code2, ZoomIn, ZoomOut, Maximize2, Expand, X } from 'lucide-react';
import 'highlight.js/styles/github-dark.css';

const REMARK = [remarkGfm];
const REHYPE = [rehypeRaw, rehypeSanitize, [rehypeHighlight, { detect: true, ignoreMissing: true }]];
const textOf = n => typeof n === 'string' ? n : Array.isArray(n) ? n.map(textOf).join('') : n?.props ? textOf(n.props.children) : '';

// Mermaid can emit HTML labels inside foreignObject. Keep those labels valid
// when exporting SVG as XML (HTML's <br> is not valid XML; it must be <br/>).
function makeStandaloneSvg(svg) {
  return String(svg || '')
    .replace(/<!DOCTYPE[^>]*>/gi, '')
    .replace(/<(br|hr|img|input|meta|link|wbr)(\s[^<>]*?)?>/gi, (tag) => {
      if (/\/\s*>$/.test(tag)) return tag;
      return tag.replace(/\s*>$/, ' />');
    });
}

function installWideDiagramStyles() {
  if (document.getElementById('chat-mermaid-wide-styles')) return;
  const style = document.createElement('style');
  style.id = 'chat-mermaid-wide-styles';
  style.textContent = `
    .msg.agent.diagram-message { width:100%; align-items:flex-start; }
    .msg.agent.diagram-message > .bubble { flex:0 1 58%; width:58%; max-width:58%; min-width:0; }
    .msg.agent.diagram-message .markdown-content { width:100%; min-width:0; }
    .chat-mermaid { width:100%; max-width:100%; min-width:0; }
    .chat-mermaid .mermaid-viewport { width:100%; max-width:100%; overflow:auto; height:min(68vh, 820px); min-height:500px; padding:8px; box-sizing:border-box; }
    .chat-mermaid .mermaid-view svg { display:block; width:100%; height:auto; max-width:none; }
    .diagram-fullscreen { position:fixed; inset:10px; z-index:99999; display:flex; flex-direction:column; min-width:0; min-height:0; padding:10px; border:1px solid #343b4b; border-radius:12px; background:#0b0d12; box-shadow:0 20px 80px #000b; }
    .diagram-fullscreen .diagram-fullscreen-tools { flex:none; display:flex; flex-wrap:wrap; gap:8px; padding:4px 4px 10px; }
    .diagram-fullscreen .diagram-fullscreen-tools button { padding:7px 12px; border:1px solid #343b4b; border-radius:7px; background:#191d26; color:#eee; }
    .diagram-fullscreen .diagram-fullscreen-canvas { flex:1; min-height:0; overflow:auto; padding:8px; }
    .diagram-fullscreen .diagram-fullscreen-canvas svg { display:block; max-width:none; height:auto; }
    body.diagram-fullscreen-open { overflow:hidden; }
    .chat-mermaid .mermaid-view { min-width:100%; width:100%; line-height:0; }
    .chat-mermaid .mermaid-view svg { display:block; width:100%; max-width:none; height:auto; preserveAspectRatio:xMidYMid meet; }
    .chat-mermaid .diagram-tools { display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
    .chat-mermaid .diagram-tools button { white-space:nowrap; }
    @media (max-width:900px) { .msg.agent.diagram-message > .bubble { flex:1 1 auto; width:calc(100% - 38px); max-width:calc(100% - 38px); } .chat-mermaid .mermaid-viewport { min-height:280px; height:60vh; } }
  `;
  document.head.appendChild(style);
}

function MermaidBlock({ source }) {
  const [svg, setSvg] = useState('');
  const [error, setError] = useState('');
  const [showSource, setShowSource] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [expanded, setExpanded] = useState(false);
  const fullViewRef = useRef(null);
  const viewRef = useRef(null);
  const wrapperRef = useRef(null);

  useEffect(() => {
    installWideDiagramStyles();
    const message = wrapperRef.current?.closest('.msg.agent');
    message?.classList.add('diagram-message');
    return () => message?.classList.remove('diagram-message');
  }, []);

  useEffect(() => {
    let live = true;
    let timeoutId;
    setSvg(''); setError(''); setZoom(1);
    timeoutId = setTimeout(() => {
      if (live) setError('Diagram rendering timed out. Show source or retry with a smaller diagram.');
    }, 30000);
    (async () => {
      try {
        const { default: mermaid } = await import('mermaid');
        mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'dark', flowchart: { htmlLabels: true, useMaxWidth: false } });
        const clean = String(source || '').replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').trim()
          .replace(/^```(?:mermaid)?\s*/i, '').replace(/\s*```\s*$/, '');
        const result = await mermaid.render(`chat-mmd-${Date.now()}-${Math.random().toString(36).slice(2)}`, clean);
        if (live) { setSvg(result.svg); setError(''); }
      } catch (e) {
        if (live) setError(e?.message || String(e));
      } finally { clearTimeout(timeoutId); }
    })();
    return () => { live = false; clearTimeout(timeoutId); };
  }, [source]);

  useEffect(() => {
    document.body.classList.toggle('diagram-fullscreen-open', expanded);
    return () => document.body.classList.remove('diagram-fullscreen-open');
  }, [expanded]);

  useEffect(() => {
    const el = viewRef.current?.querySelector('svg');
    if (el) {
      el.style.display = 'block';
      el.style.width = '100%';
      el.style.maxWidth = 'none';
      el.style.height = 'auto';
      el.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    }
    const full = fullViewRef.current?.querySelector('svg');
    if (full) { full.style.display = 'block'; full.style.width = `${zoom * 100}%`; full.style.maxWidth = 'none'; full.style.height = 'auto'; full.setAttribute('preserveAspectRatio', 'xMidYMid meet'); }
  }, [svg, zoom, expanded]);

  const standaloneHtml = () => {
    const safeSvg = makeStandaloneSvg(svg);
    return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Diagram</title><style>*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;font:14px system-ui;background:#101218;color:#eee}body{display:flex;flex-direction:column}.tools{display:flex;flex-wrap:wrap;gap:8px;padding:10px;background:#191d26;position:sticky;top:0;z-index:2}.tools button{padding:7px 12px;cursor:pointer}.viewport{flex:1;min-height:0;overflow:auto;padding:12px}.canvas{width:100%;min-width:100%;transform-origin:top left;line-height:0}svg{display:block;width:100%;height:auto;max-width:none}</style></head><body><div class="tools"><button onclick="zoomBy(1.25)">Zoom +</button><button onclick="zoomBy(.8)">Zoom −</button><button onclick="fit()">Fit width</button><button onclick="window.close()">Close tab</button></div><div class="viewport" id="viewport"><div class="canvas" id="canvas">${safeSvg}</div></div><script>let scale=1;const c=document.getElementById('canvas');function apply(){c.style.width=(100*scale)+'%'}function zoomBy(f){scale=Math.max(.2,Math.min(8,scale*f));apply()}function fit(){scale=1;apply()}document.addEventListener('keydown',e=>{if(e.key==='+'||e.key==='=')zoomBy(1.2);if(e.key==='-')zoomBy(.8333);if(e.key==='0')fit()});</script></body></html>`;
  };

  const openTab = () => {
    if (!svg) return;
    // Open a blank tab synchronously from the click, avoiding popup blockers.
    const tab = window.open('about:blank', '_blank');
    const html = standaloneHtml();
    if (tab) {
      tab.document.open(); tab.document.write(html); tab.document.close();
    } else {
      // Fallback to an HTML blob, never to an SVG/XML blob.
      const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
      const a = document.createElement('a'); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 120000);
    }
  };

  const download = () => {
    if (!svg) return;
    const safeSvg = makeStandaloneSvg(svg);
    const url = URL.createObjectURL(new Blob([safeSvg], { type: 'image/svg+xml;charset=utf-8' }));
    const a = document.createElement('a'); a.href = url; a.download = 'diagram.svg'; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  };

  return <section ref={wrapperRef} className="chat-mermaid">
    <div className="code-head"><span>Diagram preview</span><span className="row-actions diagram-tools">
      <button type="button" onClick={() => setShowSource(v => !v)}><Code2 size={13}/>{showSource ? 'Hide source' : 'View source'}</button>
      <button type="button" disabled={!svg} onClick={openTab}><ExternalLink size={13}/>Open in new tab</button>
      <button type="button" disabled={!svg} onClick={() => setZoom(z => Math.max(.2, z / 1.25))} title="Zoom out"><ZoomOut size={13}/>Zoom out</button>
      <button type="button" disabled={!svg} onClick={() => setZoom(z => Math.min(8, z * 1.25))} title="Zoom in"><ZoomIn size={13}/>Zoom in</button>
      <button type="button" disabled={!svg} onClick={() => setZoom(1)} title="Fit width"><Maximize2 size={13}/>Fit width</button>
      <button type="button" disabled={!svg} onClick={() => setExpanded(true)} title="Expand diagram to nearly full screen"><Expand size={13}/>Expand</button>
      <button type="button" disabled={!svg} onClick={download}>Download SVG</button>
    </span></div>
    {svg ? <div className="mermaid-viewport"><div ref={viewRef} className="mermaid-view" style={{ width: `${zoom * 100}%`, minWidth: `${zoom * 100}%`, lineHeight: 0 }} dangerouslySetInnerHTML={{ __html: svg }} /></div> : error ? <div className="preview-error">Could not render diagram: {error}</div> : <div className="loading"><span className="spinner"/>Rendering diagram…</div>}
    {showSource && <pre className="mermaid-source">{source}</pre>}
    {expanded && svg && <div className="diagram-fullscreen" role="dialog" aria-modal="true" aria-label="Expanded diagram preview">
      <div className="diagram-fullscreen-tools">
        <button type="button" onClick={() => setZoom(z => Math.max(.2, z / 1.25))}><ZoomOut size={14}/> Zoom out</button>
        <button type="button" onClick={() => setZoom(z => Math.min(8, z * 1.25))}><ZoomIn size={14}/> Zoom in</button>
        <button type="button" onClick={() => setZoom(1)}><Maximize2 size={14}/> Fit width</button>
        <button type="button" onClick={download}>Download SVG</button>
        <button type="button" onClick={() => setExpanded(false)} style={{marginLeft:'auto'}}><X size={14}/> Close</button>
      </div>
      <div className="diagram-fullscreen-canvas"><div ref={fullViewRef} style={{width:`${zoom * 100}%`, minWidth:`${zoom * 100}%`, lineHeight:0}} dangerouslySetInnerHTML={{__html:svg}} /></div>
    </div>}
  </section>;
}

function CodeBlock({ children, className = '' }) {
  const [copied, setCopied] = useState(false);
  const code = React.Children.toArray(children)[0];
  const lang = /language-([\w+-]+)/.exec(className || code?.props?.className || '')?.[1] || 'text';
  const value = textOf(children).replace(/\n$/, '');
  const copy = async () => {
    try { await navigator.clipboard.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1400); } catch { /* clipboard unavailable */ }
  };
  if (lang.toLowerCase() === 'mermaid') return <MermaidBlock source={value} />;
  return <div className="code-block"><div className="code-head"><span>{lang}</span><button type="button" onClick={copy} aria-label="Copy code">{copied ? <Check size={13}/> : <Copy size={13}/>} {copied ? 'Copied' : 'Copy'}</button></div><pre><code className={className}>{children}</code></pre></div>;
}

const COMPONENTS = {
  a: ({ node, ...p }) => <a {...p} target="_blank" rel="noopener noreferrer" />,
  pre: ({ node, children }) => {
    const child = React.Children.toArray(children)[0];
    const cls = child?.props?.className || '';
    return <CodeBlock className={cls}>{children}</CodeBlock>;
  },
  table: ({ node, ...p }) => <div className="markdown-table-wrap"><table {...p} /></div>,
};

function looksLikeMermaid(s) {
  const t = String(s || '').trim().replace(/^```(?:mermaid)?\s*/i, '').replace(/\s*```\s*$/, '');
  return /^(?:flowchart|graph)\s+(?:TD|TB|BT|RL|LR)\b|^sequenceDiagram\b|^classDiagram\b|^stateDiagram(?:-v2)?\b|^erDiagram\b|^journey\b|^gantt\b|^mindmap\b|^timeline\b/m.test(t) && /(?:-->|---|==>|subgraph|participant|section\s|:)/.test(t);
}

function MarkdownContent({ children }) {
  const content = String(children ?? '').replace(/\r\n?/g, '\n');
  const standaloneDiagram = useMemo(() => looksLikeMermaid(content) && !/^\s*```/m.test(content) ? content : null, [content]);
  return <div className="markdown-content">{standaloneDiagram
    ? <MermaidBlock source={standaloneDiagram} />
    : <ReactMarkdown remarkPlugins={REMARK} rehypePlugins={REHYPE} components={COMPONENTS}>{content}</ReactMarkdown>}
  </div>;
}
export default memo(MarkdownContent);
