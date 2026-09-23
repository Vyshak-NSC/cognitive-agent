from __future__ import annotations
import ast, uuid
from pathlib import Path

def extract_python(path:Path):
    text=path.read_text(encoding='utf-8',errors='replace'); tree=ast.parse(text,filename=str(path)); entities=[]; relations=[]
    module_id=str(uuid.uuid5(uuid.NAMESPACE_URL,str(path.resolve())))
    entities.append({'id':module_id,'type':'module','name':path.stem,'description':ast.get_docstring(tree) or '', 'source_file':str(path),'start':1,'end':len(text.splitlines())})
    names={}
    for n in ast.walk(tree):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
            eid=str(uuid.uuid5(uuid.NAMESPACE_URL,f'{path}:{n.lineno}:{n.name}')); names[n.name]=eid
            entities.append({'id':eid,'type':'class' if isinstance(n,ast.ClassDef) else 'function','name':n.name,'description':ast.get_docstring(n) or '', 'source_file':str(path),'start':n.lineno,'end':getattr(n,'end_lineno',n.lineno)})
            relations.append({'id':str(uuid.uuid4()),'from_id':module_id,'to_id':eid,'relation_type':'contains','description':''})
    for n in ast.walk(tree):
        if isinstance(n,(ast.Import,ast.ImportFrom)):
            for a in n.names:
                relations.append({'id':str(uuid.uuid4()),'from_id':module_id,'to_id':a.name,'relation_type':'imports','description':a.name})
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in names:
            relations.append({'id':str(uuid.uuid4()),'from_id':module_id,'to_id':names[n.func.id],'relation_type':'calls','description':n.func.id})
    return entities,relations

def extract(path:Path):
    if path.suffix.lower()=='.py': return extract_python(path)
    # Optional tree-sitter support; keep the extractor plugin boundary stable.
    try:
        from tree_sitter_languages import get_parser
        parser=get_parser(path.suffix.lstrip('.') or 'text'); tree=parser.parse(path.read_bytes())
        return [],[]
    except Exception: return [],[]
