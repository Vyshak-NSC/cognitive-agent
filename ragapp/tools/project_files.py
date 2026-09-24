"""General-purpose project file management for the agent."""
from pathlib import Path
import shutil
from ragapp.tools.definitions import Tool
from ragapp.workspace.manager import current_store
from ragapp.core.drafts import DraftManager
from ragapp.core.project_files import ProjectFileService


def _root(area):
    store = current_store()
    if area == "workspace":
        return store.workspace.resolve()
    if area == "source":
        return store.source.resolve()
    raise ValueError("area must be 'workspace' or 'source'")

def _write_root():
    # LLM-invoked writes are hard-restricted to /workspace. /source can only
    # be changed by the human UI or approval engine.
    return current_store().workspace.resolve()

def _normalise_workspace_relative_path(relative_path):
    value = str(relative_path or "").replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    if value == "workspace":
        return ""
    if value.startswith("workspace/"):
        value = value[len("workspace/"):]
    if value.startswith("/"):
        raise ValueError("Workspace path must be relative to /workspace.")
    parts = Path(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Workspace path contains an invalid path segment.")
    if parts and parts[0] in {"source", "cognition"}:
        raise ValueError(
            "This is not a workspace path. Use propose_source_file or "
            "propose_source_edit for authoritative source changes."
        )
    return value

def _write_path(relative_path):
    root = _write_root()
    value = _normalise_workspace_relative_path(relative_path)
    p = (root / value).resolve()
    p.relative_to(root)
    return p


def _path(area, relative_path):
    root = _root(area)
    p = (root / relative_path).resolve()
    p.relative_to(root)
    return p


def list_project_files(area="workspace", relative_path=""):
    root = _root(area)
    current = _path(area, relative_path)
    if not current.is_dir():
        raise NotADirectoryError(relative_path)
    base = root
    return sorted([
        {"name": p.name, "path": p.relative_to(base).as_posix(), "type": "folder" if p.is_dir() else "file"}
        for p in current.iterdir()
    ], key=lambda x: (x["type"] != "folder", x["name"].lower()))


def create_project_folder(area, relative_path):
    return ProjectFileService(current_store()).create_folder(area, relative_path)


def create_project_file(area, relative_path, content=""):
    return ProjectFileService(current_store()).write_text(area, relative_path, content)


def copy_project_item(area, source_relative_path, destination_relative_path):
    return ProjectFileService(current_store()).copy(area, source_relative_path, area, destination_relative_path)


def move_project_item(area, source_relative_path, destination_relative_path):
    return ProjectFileService(current_store()).move(area, source_relative_path, area, destination_relative_path)


def delete_project_item(area, relative_path):
    return ProjectFileService(current_store()).delete(area, relative_path)


def read_project_text(area, relative_path, encoding="utf-8"):
    if relative_path.startswith("cognition/") or relative_path == "cognition":
        raise ValueError(
            "cognition/ lives outside the source/workspace areas and its entity files are not "
            "addressed by guessed paths — use request_cognition_context or load_entities with "
            "the entity_id/name instead."
        )
    p = _path(area, relative_path)
    if not p.is_file():
        raise FileNotFoundError(relative_path)
    return {"path": relative_path, "content": p.read_text(encoding=encoding)}


def write_project_text(area, relative_path, content, encoding="utf-8"):
    return ProjectFileService(current_store()).write_text(area, relative_path, content, encoding=encoding)


def edit_project_text(area, relative_path, operation, old_text=None, new_text=None, content=None, encoding="utf-8"):
    return ProjectFileService(current_store()).edit_text(
        area, relative_path, operation, old_text=old_text, new_text=new_text,
        content=content, encoding=encoding,
    )



def _normalise_source_relative_path(relative_path):
    """Normalize an authoritative source-relative path."""
    value = str(relative_path or "").replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    if value.startswith("source/"):
        value = value[len("source/"):]
    if value.startswith("/") or not value:
        raise ValueError("Source path must be relative to /source.")
    parts = Path(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Source path contains an invalid path segment.")
    if parts[0] in {"workspace", "cognition", "source"}:
        raise ValueError(
            "Use a path relative to /source, without another area prefix."
        )
    return value


def propose_source_file(relative_path, content="", change_description="Create source file"):
    """Stage a new authoritative source file and create its review draft."""
    store = current_store()
    relative = _normalise_source_relative_path(relative_path)
    source_path = (store.source / relative).resolve()
    source_root = store.source.resolve()
    source_path.relative_to(source_root)
    if source_path.exists():
        raise FileExistsError(
            f"Source file already exists: source/{relative}. "
            "Use propose_source_edit for an existing source file."
        )

    workspace_path = (store.workspace / relative).resolve()
    workspace_root = store.workspace.resolve()
    workspace_path.relative_to(workspace_root)
    ProjectFileService(store).write_text("workspace", relative, content, overwrite=workspace_path.exists(), description=f"Stage source proposal for source/{relative}")

    draft = DraftManager(store).create(
        content=content,
        metadata={
            "target_file": f"source/{relative}",
            "target_area": "source",
            "mode": "replace",
            "change_description": change_description or f"Create source/{relative}",
            "affected_files": [f"source/{relative}"],
            "staged_workspace_file": relative,
            "operation": "create",
        },
    )
    return {
        "status": "proposed",
        "area": "source",
        "source_path": f"source/{relative}",
        "workspace_staging_path": f"workspace/{relative}",
        "draft_id": draft["id"],
        "review_status": "pending",
        "message": (
            "Source creation is staged in workspace and a pending review draft "
            "was created. It will be copied into source only after approval."
        ),
    }


def propose_source_edit(
    relative_path,
    operation,
    old_text=None,
    new_text=None,
    content=None,
    change_description="Edit source file",
    encoding="utf-8",
):
    """Stage an edit to an existing source file and create its review draft."""
    store = current_store()
    relative = _normalise_source_relative_path(relative_path)
    source_path = (store.source / relative).resolve()
    source_root = store.source.resolve()
    source_path.relative_to(source_root)
    if not source_path.is_file():
        raise FileNotFoundError(f"Source file not found: source/{relative}")

    current = source_path.read_text(encoding=encoding)
    if operation == "replace":
        if old_text is None or new_text is None or old_text not in current:
            raise ValueError("replace requires old_text/new_text and old_text must exist")
        updated = current.replace(old_text, new_text)
    elif operation == "append":
        updated = current + (content or "")
    elif operation == "prepend":
        updated = (content or "") + current
    elif operation == "replace_all":
        updated = content or ""
    else:
        raise ValueError("operation must be replace, append, prepend, or replace_all")

    workspace_path = (store.workspace / relative).resolve()
    workspace_root = store.workspace.resolve()
    workspace_path.relative_to(workspace_root)
    ProjectFileService(store).write_text("workspace", relative, updated, overwrite=workspace_path.exists(), encoding=encoding, description=f"Stage source edit for source/{relative}")

    draft = DraftManager(store).create(
        content=updated,
        metadata={
            "target_file": f"source/{relative}",
            "target_area": "source",
            "mode": "replace",
            "change_description": change_description or f"Edit source/{relative}",
            "affected_files": [f"source/{relative}"],
            "staged_workspace_file": relative,
            "operation": "edit",
        },
    )
    return {
        "status": "proposed",
        "area": "source",
        "source_path": f"source/{relative}",
        "workspace_staging_path": f"workspace/{relative}",
        "draft_id": draft["id"],
        "review_status": "pending",
        "message": (
            "Source edit is staged in workspace and a pending review draft "
            "was created. It will replace the source file only after approval."
        ),
    }

def build_project_file_tools():
    # Reads may target source or workspace. All LLM writes are workspace-only.
    area_schema={"type":"string","enum":["workspace","source"]}
    read_props={"area":area_schema,"relative_path":{"type":"string"}}
    write_props={"relative_path":{"type":"string"}}
    return [
        Tool("list_project_files", "List files/folders in source or workspace.", {"type":"object","properties":read_props,"required":["area"]}, lambda area, relative_path="": list_project_files(area, relative_path)),
        Tool("read_project_text", "Read a text file from source or workspace.", {"type":"object","properties":read_props,"required":["area","relative_path"]}, lambda area, relative_path: read_project_text(area, relative_path)),
        Tool("create_project_folder", "Create a folder in the AI workspace. Writes to authoritative source are forbidden to agent tools.", {"type":"object","properties":write_props,"required":["relative_path"]}, lambda relative_path: _workspace_create_folder(relative_path)),
        Tool("create_project_file", "Create a text file in the AI workspace.", {"type":"object","properties":{**write_props,"content":{"type":"string"}},"required":["relative_path"]}, lambda relative_path, content="": _workspace_create_file(relative_path, content)),
        Tool("copy_project_item", "Copy a file or folder within the AI workspace.", {"type":"object","properties":{"source_relative_path":{"type":"string"},"destination_relative_path":{"type":"string"}},"required":["source_relative_path","destination_relative_path"]}, lambda source_relative_path,destination_relative_path: _workspace_copy(source_relative_path,destination_relative_path)),
        Tool("move_project_item", "Move or rename a file or folder within the AI workspace.", {"type":"object","properties":{"source_relative_path":{"type":"string"},"destination_relative_path":{"type":"string"}},"required":["source_relative_path","destination_relative_path"]}, lambda source_relative_path,destination_relative_path: _workspace_move(source_relative_path,destination_relative_path)),
        Tool("delete_project_item", "Delete a file or folder from the AI workspace.", {"type":"object","properties":write_props,"required":["relative_path"]}, lambda relative_path: _workspace_delete(relative_path)),
        Tool("edit_project_text", "Edit a text file in the AI workspace using precise operations.", {"type":"object","properties":{**write_props,"operation":{"type":"string","enum":["replace","append","prepend","replace_all"]},"old_text":{"type":"string"},"new_text":{"type":"string"},"content":{"type":"string"}},"required":["relative_path","operation"]}, lambda relative_path,operation,old_text=None,new_text=None,content=None: _workspace_edit(relative_path,operation,old_text,new_text,content)),
        Tool("propose_source_file", "Create a new authoritative /source file through the review workflow. This stages the file in /workspace and immediately creates a pending draft; approval is required before /source is changed.", {"type":"object","properties":{"relative_path":{"type":"string","description":"Path relative to /source; do not prefix with source/."},"content":{"type":"string"},"change_description":{"type":"string"}},"required":["relative_path","content"]}, lambda relative_path,content="",change_description="Create source file": propose_source_file(relative_path,content,change_description)),
        Tool("propose_source_edit", "Edit an existing authoritative /source text file through the review workflow. The source file is copied to /workspace, modified there, and a pending draft is created for approval.", {"type":"object","properties":{"relative_path":{"type":"string","description":"Existing path relative to /source; do not prefix with source/."},"operation":{"type":"string","enum":["replace","append","prepend","replace_all"]},"old_text":{"type":"string"},"new_text":{"type":"string"},"content":{"type":"string"},"change_description":{"type":"string"}},"required":["relative_path","operation"]}, lambda relative_path,operation,old_text=None,new_text=None,content=None,change_description="Edit source file": propose_source_edit(relative_path,operation,old_text,new_text,content,change_description)),
    ]

def _workspace_create_folder(relative_path):
    return ProjectFileService(current_store()).create_folder("workspace", relative_path)

def _workspace_create_file(relative_path,content=""):
    return ProjectFileService(current_store()).write_text("workspace", relative_path, content)

def _workspace_copy(src,dst):
    return copy_project_item("workspace",src,dst)

def _workspace_move(src,dst):
    return move_project_item("workspace",src,dst)

def _workspace_delete(path):
    return delete_project_item("workspace",path)

def _workspace_edit(path,operation,old_text=None,new_text=None,content=None):
    return edit_project_text("workspace",path,operation,old_text,new_text,content)