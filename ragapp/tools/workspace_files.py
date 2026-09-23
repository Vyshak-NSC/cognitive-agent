"""Workspace-level file listing and safe binary copy operations."""
import shutil
from ragapp.tools.definitions import Tool
from ragapp.workspace.manager import resolve_workspace_path, get_user_workspace

def list_workspace_files(username,relative_path=""):
    root=get_user_workspace(username); current=resolve_workspace_path(username,relative_path)
    if not current.is_dir(): raise NotADirectoryError(f"Directory not found: {relative_path}")
    base=root.resolve(); entries=[]
    for p in current.iterdir(): entries.append({"name":p.name,"path":p.relative_to(base).as_posix(),"type":"folder" if p.is_dir() else "file"})
    return sorted(entries,key=lambda x:(x["type"]!="folder",x["name"].lower()))
def copy_workspace_file(username,source_relative_path,destination_relative_path):
    source=resolve_workspace_path(username,source_relative_path); dest=resolve_workspace_path(username,destination_relative_path)
    if not source.is_file(): raise FileNotFoundError(f"Source file not found: {source_relative_path}")
    if dest.exists(): raise FileExistsError(f"Destination already exists: {destination_relative_path}")
    dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,dest)
    return {"path":destination_relative_path,"status":"copied"}
def build_workspace_file_tools(username):
    return [
      Tool("list_workspace_files","List files and directories in the AI workspace. Use this before operating on an existing file when its exact path is unknown.",{"type":"object","properties":{"relative_path":{"type":"string"}},"required":[]},lambda relative_path="":list_workspace_files(username,relative_path)),
      Tool("copy_workspace_file","Copy any existing workspace file byte-for-byte, preserving its original format and metadata where supported by the filesystem.",{"type":"object","properties":{"source_relative_path":{"type":"string"},"destination_relative_path":{"type":"string"}},"required":["source_relative_path","destination_relative_path"]},lambda source_relative_path,destination_relative_path:copy_workspace_file(username,source_relative_path,destination_relative_path)),
    ]
