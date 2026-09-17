from pathlib import Path

def chunk_text(path:Path, max_chars=18000):
    text=path.read_text(encoding='utf-8',errors='replace')
    blocks=[b.strip() for b in text.split('\n\n') if b.strip()]
    out=[]; cur=''; chapter=''
    for b in blocks:
        if len(cur)+len(b)+2>max_chars and cur: out.append({'chapter':chapter,'text':cur}); cur=''
        if b.lower().startswith(('chapter ','scene ')): chapter=b.splitlines()[0][:120]
        cur=(cur+'\n\n'+b).strip()
    if cur: out.append({'chapter':chapter,'text':cur})
    return out
