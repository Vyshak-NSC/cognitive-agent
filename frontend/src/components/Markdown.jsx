import React, { memo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize from 'rehype-sanitize';
import rehypeHighlight from 'rehype-highlight';
import { Check, Copy } from 'lucide-react';
import 'highlight.js/styles/github-dark.css';

// Raw HTML in markdown is parsed (so <details>, <br>, <kbd> work) but sanitised
// before highlighting, so agent/file content can never inject scripts.
const REMARK = [remarkGfm];
const REHYPE = [rehypeRaw, rehypeSanitize, [rehypeHighlight, { detect: true, ignoreMissing: true }]];

const textOf = n => typeof n === 'string' ? n : Array.isArray(n) ? n.map(textOf).join('') : n?.props ? textOf(n.props.children) : '';

function CodeBlock({ children }) {
  const [copied, setCopied] = useState(false);
  const code = React.Children.toArray(children)[0];
  const lang = /language-([\w+-]+)/.exec(code?.props?.className || '')?.[1] || 'text';
  const copy = async () => {
    try { await navigator.clipboard.writeText(textOf(children).replace(/\n$/, '')); setCopied(true); setTimeout(() => setCopied(false), 1400); } catch { /* clipboard unavailable */ }
  };
  return <div className="code-block">
    <div className="code-head"><span>{lang}</span><button type="button" onClick={copy} aria-label="Copy code">{copied ? <Check size={13}/> : <Copy size={13}/>}{copied ? 'Copied' : 'Copy'}</button></div>
    <pre>{children}</pre>
  </div>;
}

const COMPONENTS = {
  a: ({ node, ...p }) => <a {...p} target="_blank" rel="noopener noreferrer" />,
  pre: ({ node, children }) => <CodeBlock>{children}</CodeBlock>,
  table: ({ node, ...p }) => <div className="markdown-table-wrap"><table {...p} /></div>,
};

function MarkdownContent({ children }) {
  return <div className="markdown-content">
    <ReactMarkdown remarkPlugins={REMARK} rehypePlugins={REHYPE} components={COMPONENTS}>{String(children ?? '').replace(/\r\n?/g, '\n')}</ReactMarkdown>
  </div>;
}
export default memo(MarkdownContent);
