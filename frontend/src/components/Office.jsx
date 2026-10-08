import React, { useEffect, useRef, useState } from 'react';

const MAX_ROWS = 1000;

function Sheet({ blob }) {
  const [state, setState] = useState({ sheets: null, active: 0, error: '' });
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const XLSX = await import('xlsx');
        const wb = XLSX.read(await blob.arrayBuffer(), { type: 'array' });
        // Build a React table from raw values; cell text is never injected as HTML.
        const sheets = wb.SheetNames.map(name => ({ name, rows: XLSX.utils.sheet_to_json(wb.Sheets[name], { header: 1, defval: '', raw: false }) }));
        if (live) setState({ sheets, active: 0, error: '' });
      } catch (e) { if (live) setState({ sheets: null, active: 0, error: e?.message || String(e) }); }
    })();
    return () => { live = false; };
  }, [blob]);
  if (state.error) return <pre className="preview-error">{state.error}</pre>;
  if (!state.sheets) return <div className="loading"><span className="spinner"/>Rendering…</div>;
  const sheet = state.sheets[state.active];
  return <div className="sheet-view">
    <div className="sheet-tabs">{state.sheets.map((s, i) => <button key={s.name} className={i === state.active ? 'active' : ''} onClick={() => setState(v => ({ ...v, active: i }))}>{s.name}</button>)}</div>
    <div className="sheet-scroll"><table><tbody>{sheet.rows.slice(0, MAX_ROWS).map((r, i) => <tr key={i}><th>{i + 1}</th>{r.map((c, j) => <td key={j}>{String(c)}</td>)}</tr>)}</tbody></table>
      {sheet.rows.length > MAX_ROWS && <p className="muted pad">Showing first {MAX_ROWS} of {sheet.rows.length} rows. Download for the full sheet.</p>}</div>
  </div>;
}

function Rendered({ blob, kind }) {
  const ref = useRef(null);
  const [state, setState] = useState({ loading: true, error: '' });
  useEffect(() => {
    let live = true;
    const el = ref.current;
    setState({ loading: true, error: '' });
    (async () => {
      try {
        const buf = await blob.arrayBuffer();
        if (kind === 'docx') {
          const { renderAsync } = await import('docx-preview');
          await renderAsync(buf, el, undefined, { inWrapper: true, ignoreLastRenderedPageBreak: true });
        } else {
          const { init } = await import('pptx-preview');
          const width = Math.max(480, el.clientWidth - 32);
          init(el, { width, height: Math.round(width * 9 / 16) }).preview(buf);
        }
        if (live) setState({ loading: false, error: '' });
      } catch (e) { if (live) setState({ loading: false, error: e?.message || String(e) }); }
    })();
    return () => { live = false; el.innerHTML = ''; };
  }, [blob, kind]);
  return <>{state.loading && <div className="loading"><span className="spinner"/>Rendering…</div>}{state.error && <pre className="preview-error">{state.error}</pre>}<div ref={ref} className={`office-${kind}`} /></>;
}

export default function Office({ blob, ext }) {
  return ext === '.xlsx' || ext === '.xls' ? <Sheet blob={blob} /> : <Rendered blob={blob} kind={ext === '.docx' ? 'docx' : 'pptx'} />;
}
