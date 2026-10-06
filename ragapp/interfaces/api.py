"""Authenticated HTTP API for the same project operations exposed by Streamlit."""
from __future__ import annotations

import io
import stat
import subprocess
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ragapp.agent.loop import run_agent
from ragapp.agent.run_context import AgentRunContext
from ragapp.agent.trace import ExecutionTrace
from ragapp.auth.db import initialize_database
from ragapp.auth.tokens import create_access_token, verify_access_token
from ragapp.auth.users import authenticate_user, create_user
from ragapp.chat_sessions import ChatSessionStore
from ragapp.cognition.compiler import compile_project, list_available_files
from ragapp.cognition.session_memory import SessionMemory
from ragapp.config import load_project_config, save_project_config
from ragapp.core.agents import AgentStore
from ragapp.core.approval import ApprovalEngine
from ragapp.core.drafts import DraftManager
from ragapp.core.instructions import InstructionStore
from ragapp.core.project_files import ProjectFileService
from ragapp.core.retrieval import RetrievalStore
from ragapp.core.vcs import VCSManager
from ragapp.execution.project import (
    create_project,
    delete_project,
    get_project,
    list_projects,
    rename_project,
)
from ragapp.llm.registry import DEFAULT_LIMITS, PROVIDER_SPECS
from ragapp.tools import build_default_tools
from ragapp.workspace.manager import set_current_project


app = FastAPI(
    title="Cognitive Persistence Agent API",
    version="1.0.0",
    description=(
        "Authenticated API for project, chat, cognition, file, review, "
        "instruction, history, agent, and provider-settings operations."
    ),
)
_bearer = HTTPBearer(auto_error=False)
_MAX_UPLOAD_BYTES = 500 * 1024 * 1024


@app.on_event("startup")
def _initialize_auth_database():
    initialize_database()


def _current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer token required.")
    username = verify_access_token(credentials.credentials)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired bearer token.")
    return username


def _project_store(username: str, project_id: str):
    projects = list_projects(username)
    if project_id not in projects:
        raise HTTPException(status_code=404, detail="Project not found.")
    return get_project(username, project_id)


def _api_error(exc: Exception):
    if isinstance(exc, NotADirectoryError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, FileExistsError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (ValueError, KeyError, zipfile.BadZipFile)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


def _raise_api_error(exc: Exception):
    try:
        _api_error(exc)
    except HTTPException:
        raise


class Credentials(BaseModel):
    username: str
    password: str


class ProjectCreate(BaseModel):
    project_id: str
    provider: str | None = None
    model: str | None = None
    fallback: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    api_key: str | None = None


class ProjectRename(BaseModel):
    new_project_id: str


class SessionCreate(BaseModel):
    title: str = "New chat"


class SessionRename(BaseModel):
    title: str


class ChatTurn(BaseModel):
    message: str
    parent_turn_id: str | None = None
    replacing_turn_id: str | None = None
    agent_id: str | None = None
    include_trace: bool = True


class BranchActivate(BaseModel):
    turn_id: str


class FileCreate(BaseModel):
    relative_path: str
    content: str = ""


class FolderCreate(BaseModel):
    relative_path: str


class FileEdit(BaseModel):
    operation: str = "replace_all"
    content: str | None = None
    old_text: str | None = None
    new_text: str | None = None
    encoding: str = "utf-8"


class FileTransfer(BaseModel):
    source_area: str
    source_path: str
    destination_area: str
    destination_path: str


class FileDelete(BaseModel):
    paths: list[str] = Field(min_length=1)


class CompileRequest(BaseModel):
    selected_files: list[tuple[str, str]] | None = None
    semantic_enrichment: bool = True


class RetrievalRequest(BaseModel):
    query: str
    max_chars: int = Field(default=8000, ge=1000, le=50000)
    limit: int = Field(default=8, ge=1, le=50)
    detail: str = "compact"


class DraftUpdate(BaseModel):
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DraftReject(BaseModel):
    reason: str = ""


class InstructionCreate(BaseModel):
    content: str
    scope: str = "situational"
    tagged_entity_id: str | None = None


class InstructionUpdate(BaseModel):
    content: str


class RestoreRequest(BaseModel):
    target: str = "last_compile"


class ProjectSettingsUpdate(BaseModel):
    provider: str
    model: str
    fallback: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    api_key: str | None = None
    limits: dict[str, int] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)


class AgentSpec(BaseModel):
    id: str | None = None
    name: str
    description: str = ""
    objective: str = ""
    instructions: list[str] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    output_targets: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    workflow_steps: list[dict[str, Any]] = Field(default_factory=list)
    trigger: dict[str, Any] = Field(default_factory=lambda: {"type": "manual"})
    require_mutation_approval: bool = True
    enabled: bool = True


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/auth/register", status_code=201)
def register(payload: Credentials):
    try:
        if len(payload.password) < 8:
            raise ValueError("Password must contain at least 8 characters.")
        create_user(payload.username, payload.password)
        token = create_access_token(payload.username.strip().lower())
        return {"access_token": token, "token_type": "bearer"}
    except Exception as exc:
        _raise_api_error(exc)


@app.post("/api/auth/login")
def login(payload: Credentials):
    username = authenticate_user(payload.username, payload.password)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    return {"access_token": create_access_token(username), "token_type": "bearer"}


@app.get("/api/auth/me")
def current_user(username: str = Depends(_current_user)):
    return {"username": username}


@app.get("/api/providers")
def providers(_: str = Depends(_current_user)):
    return {
        "providers": {
            name: {
                key: value
                for key, value in spec.items()
                if key not in {"key_env"}
            }
            for name, spec in PROVIDER_SPECS.items()
        },
        "default_limits": DEFAULT_LIMITS,
    }


@app.get("/api/projects")
def projects(username: str = Depends(_current_user)):
    names = list_projects(username)
    if not names:
        names = [create_project(username, "default").project_id]
    return {"projects": names}


@app.post("/api/projects", status_code=201)
def project_create(payload: ProjectCreate, username: str = Depends(_current_user)):
    try:
        selected_provider = payload.provider or "gemini"
        if selected_provider not in PROVIDER_SPECS:
            raise ValueError(f"Unsupported provider: {selected_provider}")
        if payload.provider and not (payload.model or PROVIDER_SPECS[selected_provider]["default_model"]):
            raise ValueError("A model is required for the selected provider.")
        unsupported_fallbacks = set(payload.fallback) - PROVIDER_SPECS.keys()
        if unsupported_fallbacks or selected_provider in payload.fallback:
            raise ValueError("Fallback providers must be supported and different from the primary provider.")
        store = create_project(username, payload.project_id)
        if payload.provider or payload.model or payload.endpoint or payload.api_key:
            config = load_project_config(store)
            provider = config.setdefault("provider", {})
            provider["name"] = selected_provider
            provider["model"] = payload.model or PROVIDER_SPECS[selected_provider]["default_model"]
            provider["fallback"] = payload.fallback
            if payload.endpoint:
                provider["endpoint"] = payload.endpoint
            if payload.api_key:
                slot = PROVIDER_SPECS[selected_provider]["key_slot"]
                provider.setdefault("api_keys", {})[slot] = payload.api_key
            save_project_config(store, config)
        return {"project_id": store.project_id}
    except Exception as exc:
        _raise_api_error(exc)


@app.patch("/api/projects/{project_id}")
def project_rename(
    project_id: str,
    payload: ProjectRename,
    username: str = Depends(_current_user),
):
    _project_store(username, project_id)
    try:
        store = rename_project(username, project_id, payload.new_project_id)
        return {"project_id": store.project_id}
    except Exception as exc:
        _raise_api_error(exc)


@app.delete("/api/projects/{project_id}")
def project_delete(project_id: str, username: str = Depends(_current_user)):
    _project_store(username, project_id)
    delete_project(username, project_id)
    remaining = list_projects(username)
    if not remaining:
        remaining = [create_project(username, "default").project_id]
    return {"deleted": project_id, "projects": remaining}


@app.get("/api/projects/{project_id}/overview")
def project_overview(project_id: str, username: str = Depends(_current_user)):
    store = _project_store(username, project_id)
    state = store.state_map()
    return {
        "project_id": project_id,
        "source_file_count": sum(path.is_file() for path in store.source.rglob("*")),
        "workspace_file_count": sum(path.is_file() for path in store.workspace.rglob("*")),
        "cognition_counts": store.cognition_counts(),
        "cognition_version": state.get("current_version", 0),
        "compiled": state.get("compiled", False),
    }


@app.get("/api/projects/{project_id}/sessions")
def sessions_list(project_id: str, username: str = Depends(_current_user)):
    return {"sessions": ChatSessionStore(_project_store(username, project_id)).list_sessions()}


@app.post("/api/projects/{project_id}/sessions", status_code=201)
def session_create(
    project_id: str,
    payload: SessionCreate,
    username: str = Depends(_current_user),
):
    session = ChatSessionStore(_project_store(username, project_id)).create(payload.title)
    return session


@app.get("/api/projects/{project_id}/sessions/{session_id}")
def session_get(project_id: str, session_id: str, username: str = Depends(_current_user)):
    session = ChatSessionStore(_project_store(username, project_id)).load(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    return session


@app.get("/api/projects/{project_id}/sessions/{session_id}/memory")
def session_memory(
    project_id: str,
    session_id: str,
    limit: int = 200,
    username: str = Depends(_current_user),
):
    store = _project_store(username, project_id)
    manager = ChatSessionStore(store)
    session = manager.load(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    active_turn_ids = manager.lineage_ids_data(session)
    return {
        "episodes": SessionMemory(store).list(
            session_id=session_id,
            limit=min(max(limit, 1), 1000),
            active_turn_ids=active_turn_ids,
        )
    }


@app.patch("/api/projects/{project_id}/sessions/{session_id}")
def session_rename(
    project_id: str,
    session_id: str,
    payload: SessionRename,
    username: str = Depends(_current_user),
):
    try:
        return ChatSessionStore(_project_store(username, project_id)).rename(session_id, payload.title)
    except Exception as exc:
        _raise_api_error(exc)


@app.delete("/api/projects/{project_id}/sessions/{session_id}")
def session_delete(project_id: str, session_id: str, username: str = Depends(_current_user)):
    store = _project_store(username, project_id)
    manager = ChatSessionStore(store)
    if not manager.load(session_id):
        raise HTTPException(status_code=404, detail="Chat session not found.")
    manager.delete(session_id)
    return {"deleted": session_id}


@app.post("/api/projects/{project_id}/sessions/{session_id}/branches/activate")
def session_activate_branch(
    project_id: str,
    session_id: str,
    payload: BranchActivate,
    username: str = Depends(_current_user),
):
    store = _project_store(username, project_id)
    manager = ChatSessionStore(store)
    session = manager.load(session_id)
    turn = (session.get("turns") or {}).get(payload.turn_id) if session else None
    if not turn:
        raise HTTPException(status_code=404, detail="Chat turn not found.")
    target_state = turn.get("state_after") or turn.get("state_before")
    if target_state:
        VCSManager(store).materialize_state(
            target_state,
            message=f"Activate chat branch {payload.turn_id[:8]}",
        )
    refreshed = manager.set_active_leaf(session_id, payload.turn_id)
    active_drafts = []
    for turn_id in manager.lineage_ids_data(refreshed):
        active_drafts.extend(
            ((refreshed.get("turns") or {}).get(turn_id, {}).get("effects") or {}).get("draft_ids") or []
        )
    DraftManager(store).set_session_active_drafts(session_id, active_drafts)
    return refreshed


@app.post("/api/projects/{project_id}/sessions/{session_id}/turns")
def chat_turn(
    project_id: str,
    session_id: str,
    payload: ChatTurn,
    username: str = Depends(_current_user),
):
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    store = _project_store(username, project_id)
    manager = ChatSessionStore(store)
    current = manager.load(session_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    turns = current.get("turns") or {}
    parent_id = payload.parent_turn_id
    if payload.replacing_turn_id:
        original = turns.get(payload.replacing_turn_id)
        if not original:
            raise HTTPException(status_code=404, detail="Turn to replace not found.")
        parent_id = original.get("parent_id")
        if original.get("state_before"):
            VCSManager(store).materialize_state(
                original["state_before"],
                message=f"Fork chat before {payload.replacing_turn_id[:8]}",
            )
    elif parent_id is None:
        parent_id = current.get("active_leaf_id")
    elif parent_id not in turns:
        raise HTTPException(status_code=404, detail="Parent turn not found.")

    vcs = VCSManager(store)
    state_before = vcs.checkpoint(f"Before chat turn: {payload.message[:48]}")
    turn_id = __import__("uuid").uuid4().hex
    base_transcript = manager.active_transcript_data(current, parent_id) if parent_id else []
    user_message = {"role": "user", "content": payload.message, "turn_id": turn_id}
    transcript = base_transcript + [user_message]
    active_ids = manager.lineage_ids_data(current, parent_id) if parent_id else []
    active_ids.append(turn_id)
    trace = ExecutionTrace(enabled=payload.include_trace) if payload.include_trace else None
    sections = []
    calls = []
    drafts = []
    try:
        set_current_project(store)
        tools = build_default_tools(username, store, include_cognition=True, session_id=session_id)
        answer, calls, drafts = run_agent(
            AgentRunContext(
                transcript=transcript,
                tools=tools,
                cognition=store,
                project_id=store.project_id,
                session_id=session_id,
                turn_id=turn_id,
                active_turn_ids=active_ids,
                agent_id=payload.agent_id,
                on_section=sections.append,
                trace=trace,
            )
        )
        answer = (answer or "").strip() or "The agent produced no response for this turn."
    except Exception as exc:
        answer = "The agent encountered an error while executing this request."
        calls = list(calls or []) + [{"tool": "agent_error", "args": {}, "result": str(exc)}]
    state_after = vcs.checkpoint(f"After chat turn: {payload.message[:48]}")
    tool_calls = [
        {"tool": call.get("tool"), "args": call.get("args"), "result": call.get("result")}
        for call in calls or []
        if isinstance(call, dict)
    ]
    assistant_message = {
        "role": "assistant",
        "content": answer,
        "tool_calls": tool_calls,
        "turn_id": turn_id,
    }
    if trace is not None:
        assistant_message["execution_trace"] = trace.as_dict()
    effects = {
        "draft_ids": [draft.get("id") for draft in drafts if isinstance(draft, dict)],
        "tool_count": len(calls or []),
    }
    if payload.replacing_turn_id:
        refreshed = manager.fork_turn(
            session_id,
            payload.replacing_turn_id,
            user_message,
            assistant_message,
            state_before=state_before,
            state_after=state_after,
            effects=effects,
            new_turn_id=turn_id,
            title_from=payload.message,
        )
    else:
        refreshed = manager.append_turn(
            session_id,
            user_message,
            assistant_message,
            title_from=payload.message,
            parent_id=parent_id,
            state_before=state_before,
            state_after=state_after,
            effects=effects,
            turn_id=turn_id,
        )
    active_drafts = []
    for active_turn_id in manager.lineage_ids_data(refreshed):
        active_drafts.extend(
            ((refreshed.get("turns") or {}).get(active_turn_id, {}).get("effects") or {}).get("draft_ids") or []
        )
    DraftManager(store).set_session_active_drafts(session_id, active_drafts)
    return {
        "session": refreshed,
        "turn_id": turn_id,
        "answer": answer,
        "tool_calls": tool_calls,
        "drafts": drafts,
        "sections": sections,
        "execution_trace": trace.as_dict() if trace is not None else None,
    }


@app.get("/api/projects/{project_id}/files")
def files_list(
    project_id: str,
    area: str = "workspace",
    relative_path: str = "",
    recursive: bool = False,
    username: str = Depends(_current_user),
):
    store = _project_store(username, project_id)
    try:
        if area not in {"source", "workspace"}:
            raise ValueError("area must be 'source' or 'workspace'")
        root = store.source if area == "source" else store.workspace
        base = ProjectFileService(store).path(area, relative_path)
        if not base.is_dir():
            raise NotADirectoryError(relative_path)
        paths = base.rglob("*") if recursive else base.iterdir()
        entries = []
        for path in paths:
            relative = path.relative_to(root).as_posix()
            info = path.stat()
            entries.append({
                "path": relative,
                "name": path.name,
                "type": "folder" if path.is_dir() else "file",
                "size": 0 if path.is_dir() else info.st_size,
                "modified": info.st_mtime,
            })
        entries.sort(key=lambda item: (item["type"] != "folder", item["path"].casefold()))
        return {"area": area, "relative_path": relative_path, "entries": entries}
    except Exception as exc:
        _raise_api_error(exc)


@app.post("/api/projects/{project_id}/files/folders", status_code=201)
def folder_create(
    project_id: str,
    payload: FolderCreate,
    area: str = "workspace",
    username: str = Depends(_current_user),
):
    try:
        return ProjectFileService(_project_store(username, project_id)).create_folder(area, payload.relative_path)
    except Exception as exc:
        _raise_api_error(exc)


@app.post("/api/projects/{project_id}/files", status_code=201)
def file_create(
    project_id: str,
    payload: FileCreate,
    area: str = "workspace",
    username: str = Depends(_current_user),
):
    try:
        return ProjectFileService(_project_store(username, project_id)).write_text(
            area, payload.relative_path, payload.content
        )
    except Exception as exc:
        _raise_api_error(exc)


@app.post("/api/projects/{project_id}/files/upload")
async def files_upload(
    project_id: str,
    area: str = "workspace",
    relative_path: str = "",
    files: list[UploadFile] = File(...),
    username: str = Depends(_current_user),
):
    try:
        store = _project_store(username, project_id)
        service = ProjectFileService(store)
        base = service.path(area, relative_path)
        if not base.is_dir():
            raise NotADirectoryError(relative_path)
        batch = []
        total = 0
        for upload in files:
            data = await upload.read(_MAX_UPLOAD_BYTES + 1)
            total += len(data)
            if len(data) > _MAX_UPLOAD_BYTES or total > _MAX_UPLOAD_BYTES:
                raise ValueError("Combined upload exceeds the 500 MiB limit.")
            name = Path(upload.filename or "").name
            if not name or name in {".", ".."}:
                raise ValueError("Every uploaded file must have a valid filename.")
            batch.append(((Path(relative_path) / name).as_posix(), data))
        return service.write_many(area, batch, description=f"Upload {len(batch)} file(s) to {area}")
    except Exception as exc:
        _raise_api_error(exc)


@app.post("/api/projects/{project_id}/files/extract-zip")
async def files_extract_zip(
    project_id: str,
    archive: UploadFile = File(...),
    area: str = "workspace",
    relative_path: str = "",
    overwrite: bool = Form(False),
    username: str = Depends(_current_user),
):
    try:
        archive_bytes = await archive.read(_MAX_UPLOAD_BYTES + 1)
        if len(archive_bytes) > _MAX_UPLOAD_BYTES:
            raise ValueError("ZIP upload exceeds the 500 MiB limit.")
        store = _project_store(username, project_id)
        service = ProjectFileService(store)
        base = service.path(area, relative_path)
        if not base.is_dir():
            raise NotADirectoryError(relative_path)
        batch = []
        total_uncompressed = 0
        seen_targets = set()
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zipped:
            for entry in zipped.infolist():
                if entry.is_dir():
                    continue
                path = PurePosixPath(entry.filename.replace("\\", "/"))
                if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                    raise ValueError(f"Unsafe ZIP entry: {entry.filename}")
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError(f"Symbolic link entries are not supported: {entry.filename}")
                total_uncompressed += entry.file_size
                if total_uncompressed > _MAX_UPLOAD_BYTES:
                    raise ValueError("Expanded ZIP contents exceed the 500 MiB limit.")
                target = (Path(relative_path) / Path(*path.parts)).as_posix()
                if target.casefold() in seen_targets:
                    raise ValueError(f"Duplicate ZIP entry: {target}")
                seen_targets.add(target.casefold())
                target_path = service.path(area, target)
                if target_path.exists() and not overwrite:
                    raise FileExistsError(target)
                batch.append((target, zipped.read(entry)))
        result = service.write_many(
            area,
            batch,
            overwrite=overwrite,
            description=f"Extract ZIP into {area}/{relative_path}",
        )
        return {**result, "files": [path for path, _ in batch]}
    except Exception as exc:
        _raise_api_error(exc)


@app.get("/api/projects/{project_id}/files/content")
def file_content(
    project_id: str,
    area: str,
    relative_path: str,
    offset: int = 0,
    max_chars: int = 20000,
    start_page: int | None = None,
    end_page: int | None = None,
    username: str = Depends(_current_user),
):
    try:
        from ragapp.tools.project_files import read_project_text

        set_current_project(_project_store(username, project_id))
        return read_project_text(
            area,
            relative_path,
            offset=offset,
            max_chars=max_chars,
            start_page=start_page,
            end_page=end_page,
        )
    except Exception as exc:
        _raise_api_error(exc)


@app.get("/api/projects/{project_id}/files/download")
def file_download(
    project_id: str,
    area: str,
    relative_path: str,
    username: str = Depends(_current_user),
):
    try:
        store = _project_store(username, project_id)
        path = ProjectFileService(store).path(area, relative_path)
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        return FileResponse(path, filename=path.name)
    except Exception as exc:
        _raise_api_error(exc)


@app.post("/api/projects/{project_id}/files/download-zip")
def files_download_zip(
    project_id: str,
    area: str,
    paths: list[str],
    username: str = Depends(_current_user),
):
    try:
        store = _project_store(username, project_id)
        service = ProjectFileService(store)
        buffer = io.BytesIO()
        total = 0
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipped:
            if area not in {"source", "workspace"}:
                raise ValueError("area must be 'source' or 'workspace'")
            area_root = (store.source if area == "source" else store.workspace).resolve()
            for relative in paths:
                path = service.path(area, relative)
                if not path.exists():
                    raise FileNotFoundError(relative)
                candidates = [path] if path.is_file() else sorted(path.rglob("*"))
                for item in candidates:
                    if item.is_file():
                        safe_item = service.path(area, item.relative_to(area_root).as_posix())
                        total += item.stat().st_size
                        if total > _MAX_UPLOAD_BYTES:
                            raise ValueError("ZIP download exceeds the 500 MiB limit.")
                        zipped.write(safe_item, safe_item.relative_to(area_root).as_posix())
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{area}_selection.zip"'},
        )
    except Exception as exc:
        _raise_api_error(exc)


@app.put("/api/projects/{project_id}/files/content")
def file_edit(
    project_id: str,
    payload: FileEdit,
    area: str,
    relative_path: str,
    username: str = Depends(_current_user),
):
    try:
        return ProjectFileService(_project_store(username, project_id)).edit_text(
            area,
            relative_path,
            payload.operation,
            old_text=payload.old_text,
            new_text=payload.new_text,
            content=payload.content,
            encoding=payload.encoding,
        )
    except Exception as exc:
        _raise_api_error(exc)


@app.put("/api/projects/{project_id}/files/binary")
async def file_replace_binary(
    project_id: str,
    area: str,
    relative_path: str,
    upload: UploadFile = File(...),
    username: str = Depends(_current_user),
):
    try:
        data = await upload.read(_MAX_UPLOAD_BYTES + 1)
        if len(data) > _MAX_UPLOAD_BYTES:
            raise ValueError("Upload exceeds the 500 MiB limit.")
        return ProjectFileService(_project_store(username, project_id)).write_bytes(
            area, relative_path, data, overwrite=True
        )
    except Exception as exc:
        _raise_api_error(exc)


@app.post("/api/projects/{project_id}/files/transfer")
def file_transfer(
    project_id: str,
    payload: FileTransfer,
    move: bool = False,
    username: str = Depends(_current_user),
):
    try:
        service = ProjectFileService(_project_store(username, project_id))
        if move:
            return service.move(
                payload.source_area,
                payload.source_path,
                payload.destination_area,
                payload.destination_path,
            )
        return service.copy(
            payload.source_area,
            payload.source_path,
            payload.destination_area,
            payload.destination_path,
        )
    except Exception as exc:
        _raise_api_error(exc)


@app.post("/api/projects/{project_id}/files/delete")
def files_delete(
    project_id: str,
    payload: FileDelete,
    area: str = "workspace",
    username: str = Depends(_current_user),
):
    try:
        return ProjectFileService(_project_store(username, project_id)).delete_many(
            area, payload.paths, description=f"Delete {len(payload.paths)} item(s) from {area}"
        )
    except Exception as exc:
        _raise_api_error(exc)


@app.get("/api/projects/{project_id}/cognition/files")
def cognition_files(project_id: str, username: str = Depends(_current_user)):
    return {"files": list_available_files(_project_store(username, project_id))}


@app.post("/api/projects/{project_id}/cognition/compile")
def cognition_compile(
    project_id: str,
    payload: CompileRequest,
    username: str = Depends(_current_user),
):
    try:
        store = _project_store(username, project_id)
        available = {(item["area"], item["path"]) for item in list_available_files(store)}
        selected = payload.selected_files
        if selected is not None:
            invalid = [item for item in selected if tuple(item) not in available]
            if invalid:
                raise ValueError(f"Selected files do not exist or are not compilable: {invalid}")
        result = compile_project(
            store,
            selected_files=selected,
            semantic_enrichment=payload.semantic_enrichment,
        )
        return {
            **result,
            "cognition_counts": store.cognition_counts(),
            "state": store.state_map(),
        }
    except Exception as exc:
        _raise_api_error(exc)


@app.get("/api/projects/{project_id}/cognition")
def cognition_state(project_id: str, username: str = Depends(_current_user)):
    store = _project_store(username, project_id)
    return {
        "counts": store.cognition_counts(),
        "state": store.state_map(),
        "ledger": store.ledger(),
        "relationships": store.relationships(),
        "events": store.events(),
    }


@app.post("/api/projects/{project_id}/cognition/retrieve")
def cognition_retrieve(
    project_id: str,
    payload: RetrievalRequest,
    username: str = Depends(_current_user),
):
    store = _project_store(username, project_id)
    detail = payload.detail.strip().lower()
    if detail not in {"compact", "full"}:
        raise HTTPException(status_code=400, detail="detail must be 'compact' or 'full'.")
    try:
        return RetrievalStore(store).retrieve_for_query(
            payload.query,
            max_chars=payload.max_chars,
            limit=payload.limit,
            detail=detail,
        )
    except Exception as exc:
        _raise_api_error(exc)


@app.get("/api/projects/{project_id}/review/drafts")
def drafts_list(
    project_id: str,
    status: str | None = None,
    username: str = Depends(_current_user),
):
    store = _project_store(username, project_id)
    return {"drafts": DraftManager(store).list(status=status)}


@app.patch("/api/projects/{project_id}/review/drafts/{draft_id}")
def draft_update(
    project_id: str,
    draft_id: str,
    payload: DraftUpdate,
    username: str = Depends(_current_user),
):
    try:
        manager = DraftManager(_project_store(username, project_id))
        draft = manager.load(draft_id)
        if draft.get("status") != "pending":
            raise ValueError("Only pending drafts can be edited.")
        draft["content"] = payload.content
        draft["metadata"] = {**(draft.get("metadata") or {}), **payload.metadata}
        return manager.save(draft)
    except Exception as exc:
        _raise_api_error(exc)


@app.post("/api/projects/{project_id}/review/drafts/{draft_id}/approve")
def draft_approve(project_id: str, draft_id: str, username: str = Depends(_current_user)):
    try:
        return ApprovalEngine(_project_store(username, project_id)).approve(draft_id)
    except Exception as exc:
        _raise_api_error(exc)


@app.post("/api/projects/{project_id}/review/drafts/{draft_id}/reject")
def draft_reject(
    project_id: str,
    draft_id: str,
    payload: DraftReject,
    username: str = Depends(_current_user),
):
    try:
        return ApprovalEngine(_project_store(username, project_id)).reject(draft_id, payload.reason)
    except Exception as exc:
        _raise_api_error(exc)


@app.get("/api/projects/{project_id}/instructions")
def instructions_list(project_id: str, username: str = Depends(_current_user)):
    return {"instructions": InstructionStore(_project_store(username, project_id)).list()}


@app.post("/api/projects/{project_id}/instructions", status_code=201)
def instruction_add(
    project_id: str,
    payload: InstructionCreate,
    username: str = Depends(_current_user),
):
    if payload.scope not in {"system", "file_tagged", "situational"}:
        raise HTTPException(status_code=400, detail="Invalid instruction scope.")
    if payload.scope == "file_tagged" and not payload.tagged_entity_id:
        raise HTTPException(status_code=400, detail="tagged_entity_id is required for file_tagged scope.")
    store = _project_store(username, project_id)
    instruction_id = InstructionStore(store).add(
        payload.content.strip(),
        payload.scope,
        payload.tagged_entity_id,
        "user_stated",
    )
    return {"id": instruction_id}


@app.patch("/api/projects/{project_id}/instructions/{instruction_id}")
def instruction_update(
    project_id: str,
    instruction_id: str,
    payload: InstructionUpdate,
    username: str = Depends(_current_user),
):
    manager = InstructionStore(_project_store(username, project_id))
    if not any(item["id"] == instruction_id for item in manager.list("active")):
        raise HTTPException(status_code=404, detail="Active instruction not found.")
    manager.update(instruction_id, payload.content.strip())
    return {"id": instruction_id, "status": "updated"}


@app.post("/api/projects/{project_id}/instructions/{instruction_id}/deactivate")
def instruction_deactivate(
    project_id: str,
    instruction_id: str,
    username: str = Depends(_current_user),
):
    manager = InstructionStore(_project_store(username, project_id))
    if not any(item["id"] == instruction_id for item in manager.list("active")):
        raise HTTPException(status_code=404, detail="Active instruction not found.")
    manager.deactivate(instruction_id)
    return {"id": instruction_id, "status": "deactivated"}


@app.get("/api/projects/{project_id}/history")
def project_history(
    project_id: str,
    limit: int = 50,
    username: str = Depends(_current_user),
):
    vcs = VCSManager(_project_store(username, project_id))
    return {"log": vcs.log(min(max(limit, 1), 400)), "states": vcs.list_states(limit=min(max(limit, 1), 100))}


@app.post("/api/projects/{project_id}/history/checkpoint")
def project_checkpoint(
    project_id: str,
    message: str = "API checkpoint",
    username: str = Depends(_current_user),
):
    try:
        commit = VCSManager(_project_store(username, project_id)).checkpoint(message)
        return {"commit": commit}
    except Exception as exc:
        _raise_api_error(exc)


def _resolve_history_commit(vcs: VCSManager, commit: str) -> str:
    if commit.lower() != "head" and (
        len(commit) not in range(7, 41)
        or any(character not in "0123456789abcdefABCDEF" for character in commit)
    ):
        raise ValueError("Commit must be HEAD or a 7-40 character hexadecimal Git hash.")
    try:
        return vcs.resolve_state(commit)
    except subprocess.CalledProcessError as exc:
        raise ValueError("Commit was not found in this project's history.") from exc


@app.get("/api/projects/{project_id}/history/file")
def file_history(
    project_id: str,
    path: str,
    limit: int = 50,
    username: str = Depends(_current_user),
):
    try:
        vcs = VCSManager(_project_store(username, project_id))
        return {"path": path, "history": vcs.file_history(path, min(max(limit, 1), 400))}
    except Exception as exc:
        _raise_api_error(exc)


@app.get("/api/projects/{project_id}/history/revision")
def historical_file(
    project_id: str,
    commit: str,
    path: str,
    username: str = Depends(_current_user),
):
    try:
        vcs = VCSManager(_project_store(username, project_id))
        content = vcs.show_file_at_bytes(_resolve_history_commit(vcs, commit), path)
        return StreamingResponse(
            io.BytesIO(content),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{Path(path).name}"'},
        )
    except Exception as exc:
        _raise_api_error(exc)


@app.get("/api/projects/{project_id}/history/diff")
def history_diff(
    project_id: str,
    before: str,
    after: str,
    path: str | None = None,
    username: str = Depends(_current_user),
):
    try:
        vcs = VCSManager(_project_store(username, project_id))
        return {
            "diff": vcs.diff(
                _resolve_history_commit(vcs, before),
                _resolve_history_commit(vcs, after),
                path,
            )
        }
    except Exception as exc:
        _raise_api_error(exc)


@app.post("/api/projects/{project_id}/history/restore-file")
def history_restore_file(
    project_id: str,
    commit: str,
    path: str,
    username: str = Depends(_current_user),
):
    try:
        vcs = VCSManager(_project_store(username, project_id))
        return vcs.restore_file(_resolve_history_commit(vcs, commit), path)
    except Exception as exc:
        _raise_api_error(exc)


@app.get("/api/projects/{project_id}/history/states")
def project_states(
    project_id: str,
    limit: int = 15,
    kind: str = "all",
    username: str = Depends(_current_user),
):
    if kind not in {"all", "compile"}:
        raise HTTPException(status_code=400, detail="kind must be 'all' or 'compile'.")
    return {"states": VCSManager(_project_store(username, project_id)).list_states(limit, kind)}


@app.post("/api/projects/{project_id}/history/restore")
def project_restore(
    project_id: str,
    payload: RestoreRequest,
    username: str = Depends(_current_user),
):
    try:
        return VCSManager(_project_store(username, project_id)).restore_project_state(payload.target)
    except Exception as exc:
        _raise_api_error(exc)


@app.get("/api/projects/{project_id}/settings")
def project_settings(project_id: str, username: str = Depends(_current_user)):
    config = load_project_config(_project_store(username, project_id))
    provider = dict(config.get("provider") or {})
    keys = provider.pop("api_keys", {}) or {}
    provider["configured_api_key_slots"] = sorted(keys.keys())
    return {"provider": provider, "limits": config.get("limits", {}), "features": config.get("features", {})}


@app.put("/api/projects/{project_id}/settings")
def project_settings_update(
    project_id: str,
    payload: ProjectSettingsUpdate,
    username: str = Depends(_current_user),
):
    if payload.provider not in PROVIDER_SPECS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {payload.provider}")
    unsupported = set(payload.fallback) - PROVIDER_SPECS.keys()
    if unsupported or payload.provider in payload.fallback:
        raise HTTPException(status_code=400, detail="Fallback providers must be supported and different from the primary provider.")
    if set(payload.limits) - DEFAULT_LIMITS.keys():
        raise HTTPException(status_code=400, detail="Unknown project limit.")
    config = load_project_config(_project_store(username, project_id))
    provider = config.setdefault("provider", {})
    provider.update(name=payload.provider, model=payload.model, fallback=payload.fallback)
    if payload.endpoint is not None:
        provider["endpoint"] = payload.endpoint
    if payload.api_key:
        slot = PROVIDER_SPECS[payload.provider]["key_slot"]
        provider.setdefault("api_keys", {})[slot] = payload.api_key
    config["limits"] = {**(config.get("limits") or {}), **payload.limits}
    config["features"] = {**(config.get("features") or {}), **payload.features}
    save_project_config(_project_store(username, project_id), config)
    return project_settings(project_id, username)


@app.get("/api/projects/{project_id}/agents")
def agents_list(
    project_id: str,
    enabled_only: bool = False,
    username: str = Depends(_current_user),
):
    return {"agents": AgentStore(_project_store(username, project_id)).list(enabled_only=enabled_only)}


@app.put("/api/projects/{project_id}/agents")
def agent_save(
    project_id: str,
    payload: AgentSpec,
    username: str = Depends(_current_user),
):
    return AgentStore(_project_store(username, project_id)).save(payload.model_dump(exclude_none=True))


@app.delete("/api/projects/{project_id}/agents/{agent_id}")
def agent_delete(project_id: str, agent_id: str, username: str = Depends(_current_user)):
    if not AgentStore(_project_store(username, project_id)).delete(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found.")
    return {"deleted": agent_id}
