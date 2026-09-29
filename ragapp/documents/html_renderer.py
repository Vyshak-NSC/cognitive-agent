from __future__ import annotations
from html import escape
from .themes import get_theme

def render_html(model,path,theme_name='professional'):
    t=get_theme(theme_name); parts=[]
    for b in model.blocks:
        if b.kind=='heading': parts.append(f'<h{min(6,b.level or 1)}>{escape(b.text)}</h{min(6,b.level or 1)}>')
        elif b.kind=='paragraph': parts.append(f'<p>{escape(b.text)}</p>')
        elif b.kind=='callout': parts.append(f'<aside>{escape(b.text)}</aside>')
        elif b.kind=='list_item': parts.append(f'<p class="list">• {escape(b.text)}</p>')
        elif b.kind=='table':
            rows=[]
            for i,row in enumerate(b.rows):
                tag='th' if i==0 else 'td'; rows.append('<tr>'+''.join(f'<{tag}>{escape(str(c))}</{tag}>' for c in row)+'</tr>')
            parts.append('<table>'+''.join(rows)+'</table>')
    css=f'''body{{font-family:Inter,Arial,sans-serif;max-width:900px;margin:48px auto;padding:0 28px;line-height:1.55;color:#1f2933}}h1{{font-size:2rem;border-bottom:2px solid #263746;padding-bottom:.35em}}h2{{margin-top:1.8em}}p{{margin:.55em 0}}aside{{background:#f1f3f5;border-left:4px solid #263746;padding:12px 16px;margin:16px 0}}table{{width:100%;border-collapse:collapse;margin:18px 0;font-size:.9rem}}th,td{{border:1px solid #ccd2d8;padding:8px;vertical-align:top;text-align:left}}th{{background:#f1f3f5}}@media print{{body{{margin:0;max-width:none}}h1,h2,h3{{break-after:avoid}}table{{break-inside:auto}}tr{{break-inside:avoid}}}}'''
    title=escape(model.title or 'Document'); html='<!doctype html><html><head><meta charset="utf-8"><title>'+title+'</title><style>'+css+'</style></head><body>'+''.join(parts)+'</body></html>'
    path.write_text(html,encoding='utf-8'); return path
