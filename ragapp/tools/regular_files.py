"""Tools for ordinary text/code/config files in the AI workspace."""
from pathlib import Path

from ragapp.tools.definitions import Tool
from ragapp.workspace.manager import resolve_workspace_path

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".py", ".js", ".jsx", ".ts", ".tsx",
    ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb",
    ".php", ".sh", ".bash", ".zsh", ".ps1", ".sql", ".html", ".htm",
    ".css", ".scss", ".svg", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".properties", ".env", ".log", ".csv",
    ".tsv", ".tex", ".rst", ".gitignore", ".dockerfile",
}


def _path(username, relative_path):
    return resolve_workspace_path(username, relative_path)


def read_regular_file(username, relative_path, encoding="utf-8"):
    if relative_path.startswith("cognition/") or relative_path == "cognition":
        raise ValueError(
            "cognition/ is not reachable via read_regular_file (it only sees the AI workspace, "
            "and entity data is not addressed by guessed file paths anyway) — "
            "use request_cognition_context or load_entities with the entity_id/name instead."
        )
    path = _path(username, relative_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {relative_path}")
    if path.suffix.lower() not in TEXT_EXTENSIONS and path.name.lower() not in TEXT_EXTENSIONS:
        raise ValueError(f"Unsupported regular text file type: {path.suffix or path.name}")
    return {"path": relative_path, "content": path.read_text(encoding=encoding)}


def write_regular_file(username, relative_path, content, encoding="utf-8"):
    path = _path(username, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"File already exists: {relative_path}")
    path.write_text(content, encoding=encoding)
    return {"path": relative_path, "status": "created"}


def edit_regular_file(username, relative_path, operation, old_text=None, new_text=None, content=None, encoding="utf-8"):
    path = _path(username, relative_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {relative_path}")
    current = path.read_text(encoding=encoding)
    if operation == "replace":
        if old_text is None or new_text is None:
            raise ValueError("replace requires old_text and new_text")
        if old_text not in current:
            raise ValueError("old_text was not found in the file")
        updated = current.replace(old_text, new_text)
    elif operation == "append":
        if content is None:
            raise ValueError("append requires content")
        updated = current + content
    elif operation == "prepend":
        if content is None:
            raise ValueError("prepend requires content")
        updated = content + current
    elif operation == "replace_all":
        if content is None:
            raise ValueError("replace_all requires content")
        updated = content
    else:
        raise ValueError("operation must be replace, append, prepend, or replace_all")
    path.write_text(updated, encoding=encoding)
    return {"path": relative_path, "status": "edited"}


def delete_regular_file(username, relative_path):
    path = _path(username, relative_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {relative_path}")
    path.unlink()
    return {"path": relative_path, "status": "deleted"}


def build_regular_file_tools(username):
    return [
        Tool("read_regular_file", "Read a text/code/config file without changing its formatting or bytes beyond decoding as text.", {
            "type": "object", "properties": {"relative_path": {"type": "string"}}, "required": ["relative_path"]
        }, lambda relative_path: read_regular_file(username, relative_path)),
        Tool("write_regular_file", "Create a new text/code/config file. Use the exact requested extension and content.", {
            "type": "object", "properties": {"relative_path": {"type": "string"}, "content": {"type": "string"}}, "required": ["relative_path", "content"]
        }, lambda relative_path, content: write_regular_file(username, relative_path, content)),
        Tool("edit_regular_file", "Edit an existing text/code/config file using a precise operation.", {
            "type": "object", "properties": {
                "relative_path": {"type": "string"},
                "operation": {"type": "string", "enum": ["replace", "append", "prepend", "replace_all"]},
                "old_text": {"type": "string"}, "new_text": {"type": "string"}, "content": {"type": "string"}
            }, "required": ["relative_path", "operation"]
        }, lambda relative_path, operation, old_text=None, new_text=None, content=None: edit_regular_file(username, relative_path, operation, old_text, new_text, content)),
        Tool("delete_regular_file", "Delete an existing text/code/config file from the AI workspace.", {
            "type": "object", "properties": {"relative_path": {"type": "string"}}, "required": ["relative_path"]
        }, lambda relative_path: delete_regular_file(username, relative_path)),
    ]