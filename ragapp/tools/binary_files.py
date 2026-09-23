"""Last-resort byte-level tools for file types without a semantic format handler."""
import base64
from ragapp.tools.definitions import Tool
from ragapp.workspace.manager import resolve_workspace_path

def _path(u,p): return resolve_workspace_path(u,p)
def read_binary_file(username,relative_path,max_bytes=65536):
    path=_path(username,relative_path)
    if not path.is_file(): raise FileNotFoundError(f"File not found: {relative_path}")
    data=path.read_bytes()
    if len(data)>max_bytes: raise ValueError(f"File is {len(data)} bytes; max_bytes is {max_bytes}. Use a format-specific tool when available.")
    return {"path":relative_path,"size":len(data),"base64":base64.b64encode(data).decode("ascii")}
def write_binary_file(username,relative_path,base64_content):
    path=_path(username,relative_path); path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists(): raise FileExistsError(f"File already exists: {relative_path}")
    try: data=base64.b64decode(base64_content,validate=True)
    except Exception as exc: raise ValueError("base64_content is invalid") from exc
    path.write_bytes(data); return {"path":relative_path,"status":"created","bytes":len(data)}
def edit_binary_file(username,relative_path,offset,delete_bytes=0,insert_base64=""):
    path=_path(username,relative_path)
    if not path.is_file(): raise FileNotFoundError(f"File not found: {relative_path}")
    data=path.read_bytes()
    if offset<0 or offset>len(data) or delete_bytes<0 or offset+delete_bytes>len(data): raise ValueError("Invalid byte range")
    try: insert=base64.b64decode(insert_base64,validate=True) if insert_base64 else b""
    except Exception as exc: raise ValueError("insert_base64 is invalid") from exc
    path.write_bytes(data[:offset]+insert+data[offset+delete_bytes:]); return {"path":relative_path,"status":"edited","bytes":path.stat().st_size}
def delete_binary_file(username,relative_path):
    path=_path(username,relative_path)
    if not path.is_file(): raise FileNotFoundError(f"File not found: {relative_path}")
    path.unlink(); return {"path":relative_path,"status":"deleted"}
def build_binary_file_tools(username):
    return [
      Tool("read_binary_file","Read an unsupported binary file as base64. Use a format-specific tool instead whenever one exists.",{"type":"object","properties":{"relative_path":{"type":"string"},"max_bytes":{"type":"integer"}},"required":["relative_path"]},lambda relative_path,max_bytes=65536:read_binary_file(username,relative_path,max_bytes)),
      Tool("write_binary_file","Create an arbitrary binary file from base64 bytes when no semantic format-specific writer exists.",{"type":"object","properties":{"relative_path":{"type":"string"},"base64_content":{"type":"string"}},"required":["relative_path","base64_content"]},lambda relative_path,base64_content:write_binary_file(username,relative_path,base64_content)),
      Tool("edit_binary_file","Perform a precise byte-range edit on an unsupported binary file. Use only when no semantic format tool exists.",{"type":"object","properties":{"relative_path":{"type":"string"},"offset":{"type":"integer"},"delete_bytes":{"type":"integer"},"insert_base64":{"type":"string"}},"required":["relative_path","offset"]},lambda relative_path,offset,delete_bytes=0,insert_base64="":edit_binary_file(username,relative_path,offset,delete_bytes,insert_base64)),
      Tool("delete_binary_file","Delete an arbitrary unsupported binary file from the AI workspace.",{"type":"object","properties":{"relative_path":{"type":"string"}},"required":["relative_path"]},lambda relative_path:delete_binary_file(username,relative_path)),
    ]
