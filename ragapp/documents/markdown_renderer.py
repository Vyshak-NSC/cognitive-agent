from __future__ import annotations

def render_markdown(model,path,theme_name='professional'):
    out=[]
    for b in model.blocks:
        if b.kind=='heading': out += ['#'*min(6,b.level or 1)+' '+b.text,'']
        elif b.kind=='paragraph': out += [b.text,'']
        elif b.kind=='callout': out += ['> '+b.text,'']
        elif b.kind=='list_item': out += [('1. ' if b.ordered else '- ')+b.text]
        elif b.kind=='table' and b.rows:
            width=max(len(r) for r in b.rows); rows=[r+['']*(width-len(r)) for r in b.rows]
            out += ['| '+' | '.join(rows[0])+' |','| '+' | '.join(['---']*width)+' |']
            out += ['| '+' | '.join(r)+' |' for r in rows[1:]]; out.append('')
    path.write_text('\n'.join(out),encoding='utf-8'); return path
