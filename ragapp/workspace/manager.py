from pathlib import Path
import shutil
from contextvars import ContextVar
from ragapp.cognition.store import CognitionStore

_current:ContextVar[CognitionStore|None]=ContextVar("current_cognition",default=None)

def set_current_project(store): _current.set(store)
def current_store():
    s=_current.get()
    if s is None: raise RuntimeError("No active project context")
    return s

def get_user_documents_root(username):
    return current_store().source

def get_user_workspace(username):
    return current_store().workspace

def _normalise_relative_path(relative_path, area):
    """Normalize a path relative to a project area.

    Agent workspace tools are rooted at /workspace, so a redundant
    ``workspace/`` prefix is removed. Source paths are handled separately.
    """
    value = str(relative_path or "").replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    if value.startswith("/"):
        raise ValueError(f"{area} path must be relative to /{area}.")
    prefix = f"{area}/"
    if value == area:
        value = ""
    elif value.startswith(prefix):
        value = value[len(prefix):]
    parts = Path(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{area} path contains an invalid path segment.")
    if parts and parts[0] in {"source", "workspace", "cognition"}:
        raise ValueError(f"{relative_path!r} is not a {area} path.")
    return value

def _resolve_inside(root, relative_path, area="workspace"):
    root = root.resolve()
    value = _normalise_relative_path(relative_path, area)
    resolved = (root / value).resolve()
    resolved.relative_to(root)
    return resolved

def resolve_workspace_path(username, relative_path):
    return _resolve_inside(get_user_workspace(username), relative_path, "workspace")

def resolve_source_path(username, relative_path):
    return _resolve_inside(get_user_documents_root(username), relative_path, "source")

def list_document_tree(username,relative_path=""):
    root=get_user_documents_root(username); cur=_resolve_inside(root,relative_path,"source"); base=root.resolve()
    if not cur.is_dir(): raise NotADirectoryError(relative_path)
    return sorted([{"name":p.name,"path":p.relative_to(base).as_posix(),"type":"folder" if p.is_dir() else "file"} for p in cur.iterdir()],key=lambda e:(e['type']!='folder',e['name'].lower()))
def list_document_files(username): return [p.relative_to(get_user_documents_root(username)).as_posix() for p in get_user_documents_root(username).rglob('*') if p.is_file()]
def delete_document(username,relative_path):
    p=_resolve_inside(get_user_documents_root(username),relative_path,"source")
    if not p.is_file(): raise FileNotFoundError(relative_path)
    p.unlink(); return p
def add_document_to_workspace(username,source_relative_path,workspace_relative_path=""):
    src=_resolve_inside(get_user_documents_root(username),source_relative_path,"source"); dst_dir=_resolve_inside(get_user_workspace(username),workspace_relative_path,"workspace")
    if not src.is_file(): raise FileNotFoundError(source_relative_path)
    dst_dir.mkdir(parents=True,exist_ok=True); dst=dst_dir/src.name
    if dst.exists(): raise FileExistsError(dst.name)
    shutil.copy2(src,dst); return dst
def remove_document_from_workspace(username,relative_path):
    p=resolve_workspace_path(username,relative_path); p.unlink(); return p
def list_workspace_tree(username,relative_path=""):
    root=get_user_workspace(username); cur=_resolve_inside(root,relative_path); base=root.resolve()
    return sorted([{"name":p.name,"path":p.relative_to(base).as_posix(),"type":"folder" if p.is_dir() else "file"} for p in cur.iterdir()],key=lambda e:(e['type']!='folder',e['name'].lower()))
