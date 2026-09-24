"""HTTP API for the Cognitive Persistence Agent.

The API is the frontend-independent application boundary. Streamlit and any
React/desktop/mobile client should express the same project operations through
these services rather than duplicating persistence semantics in the UI.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ragapp.agent.loop import run_agent
from ragapp.auth.db import initialize_database
from ragapp.auth.tokens import create_access_token, verify_access_token
from ragapp.auth.users import authenticate_user, create_user, is_admin_user
from ragapp.chat_sessions import ChatSessionStore
from ragapp.cognition.compiler import compile_project, list_available_files
from ragapp.cognition.session_memory import SessionMemory
from ragapp.config import load_project_config, save_project_config
from ragapp.core.approval import ApprovalEngine
from ragapp.core.drafts import DraftManager
from ragapp.core.instructions import InstructionStore
from ragapp.core.vcs import VCSManager
from ragapp.execution.project import (
    create_project,
    delete_project,
    get_project,
    list_projects,
    rename_project,
)
from ragapp.tools import build_default_tools
from ragapp.tools.cognition_tools import _impact, _validate, _world_model_snapshot
from ragapp.workspace.manager import set_current_project

API_VERSION = "8.6.0"
app = FastAPI(title="Cognitive Persistence Agent API", version=API_VERSION)
initialize_database()

# Local React/Vite defaults. Production deployments should override CORS_ORIGINS.
_origins = [x.strip() for x in os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
).split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Area = Literal["workspace", "source"]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(LoginRequest):
    pass


class ProjectCreate(BaseModel):
    project_id: str
    provider: str | None = None
    model: str | None = None
    fallback: list[str] = Field(default_factory=list)
    api_key: str | None = None


class ProjectRename(BaseModel):
    new_project_id: str


class QueryRequest(BaseModel):
    messages: list[dict[str, Any]]
    session_id: str | None = None


class SessionCreate(BaseModel):
    title: str = "New chat"


class SessionRename(BaseModel):
    title: str


class FileCreate(BaseModel):
    path: str
    kind: Literal["file", "folder"] = "file"
    content: str = ""


class FileTextUpdate(BaseModel):
    content: str


class FileRenameMove(BaseModel):
    destination_path: str


class FileTransfer(BaseModel):
    source_area: Area
    source_path: str
    destination_area: Area
    destination_path: str
    operation: Literal["copy", "move"] = "copy"


class CompileRequest(BaseModel):
    selected_files: list[tuple[Area, str]] | None = None


class RejectRequest(BaseModel):
    reason: str = ""


class DraftEdit(BaseModel):
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class InstructionCreate(BaseModel):
    content: str
    scope: str = "situational"
    tagged_entity_id: str | None = None


class InstructionUpdate(BaseModel):
    content: str


class ConfigUpdate(BaseModel):
    config: dict[str, Any]


# ---------------------------------------------------------------------------
# Auth / ownership
# ---------------------------------------------------------------------------
def current_user(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing Bearer token")
    token = authorization.split(" ", 1)[1].strip()
    username = verify_access_token(token)
    if not username:
        raise HTTPException(401, "Invalid or expired token")
    return username


def admin_user(username: str = Depends(current_user)) -> str:
    if not is_admin_user(username):
        raise HTTPException(403, "Administrator access required")
    return username


def store_for(username: str, project_id: str):
    # Do not use get_project() as an existence check because it initializes a
    # missing project. Validate against the user's project list first.
    if project_id not in list_projects(username):
        raise HTTPException(404, f"Project '{project_id}' not found")
    return get_project(username, project_id)


def _safe_area_path(store, area: Area, relative: str = "") -> Path:
    root = (store.workspace if area == "workspace" else store.source).resolve()
    root.mkdir(parents=True, exist_ok=True)
    clean = str(relative or "").replace("\\", "/").lstrip("/")
    target = (root / clean).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(400, "Path escapes project area") from exc
    return target


def _relative(root: Path, p: Path) -> str:
    return p.relative_to(root.resolve()).as_posix()


def _entry(root: Path, p: Path) -> dict[str, Any]:
    stat = p.stat()
    return {
        "name": p.name,
        "path": _relative(root, p),
        "type": "folder" if p.is_dir() else "file",
        "size": None if p.is_dir() else stat.st_size,
        "modified_at": stat.st_mtime,
    }


# ---------------------------------------------------------------------------
# System + authentication
# ---------------------------------------------------------------------------
@app.get("/api/v1/health")
def health():
    return {"ok": True, "version": API_VERSION}


@app.post("/api/v1/auth/login")
def login(body: LoginRequest):
    username = authenticate_user(body.username, body.password)
    if not username:
        raise HTTPException(401, "Invalid username or password")
    return {"access_token": create_access_token(username), "token_type": "bearer", "username": username}


@app.post("/api/v1/auth/register", status_code=201)
def register(body: RegisterRequest):
    if len(body.password) < 8:
        raise HTTPException(400, "Password must contain at least 8 characters")
    try:
        create_user(body.username, body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    username = authenticate_user(body.username, body.password)
    return {"access_token": create_access_token(username), "token_type": "bearer", "username": username}


@app.get("/api/v1/auth/me")
def me(username: str = Depends(current_user)):
    return {"username": username, "is_admin": is_admin_user(username)}


@app.post("/api/v1/system/shutdown", dependencies=[Depends(admin_user)])
async def shutdown_server():
    """Gracefully terminate the API process after the response is sent."""
    async def stop_later():
        await asyncio.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)
    asyncio.create_task(stop_later())
    return {"ok": True, "message": "API server is shutting down"}


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
@app.get("/api/v1/projects")
def projects(username: str = Depends(current_user)):
    return {"projects": list_projects(username)}


@app.post("/api/v1/projects", status_code=201)
def project_create(body: ProjectCreate, username: str = Depends(current_user)):
    try:
        store = create_project(username, body.project_id)
        if body.provider or body.model or body.fallback or body.api_key:
            cfg = load_project_config(store)
            prov = cfg.setdefault("provider", {})
            if body.provider:
                prov["name"] = body.provider
            if body.model is not None:
                prov["model"] = body.model
            prov["fallback"] = list(body.fallback)
            if body.api_key and body.provider:
                prov.setdefault("api_keys", {})[body.provider] = body.api_key
            save_project_config(store, cfg)
        return {"project_id": store.project_id}
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(409 if isinstance(exc, FileExistsError) else 400, str(exc)) from exc


@app.patch("/api/v1/projects/{project_id}")
def project_rename(project_id: str, body: ProjectRename, username: str = Depends(current_user)):
    store_for(username, project_id)
    try:
        store = rename_project(username, project_id, body.new_project_id)
        return {"project_id": store.project_id}
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/v1/projects/{project_id}")
def project_delete(project_id: str, username: str = Depends(current_user)):
    store_for(username, project_id)
    delete_project(username, project_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# File manager: Workspace + Source
# ---------------------------------------------------------------------------
@app.get("/api/v1/projects/{project_id}/files/{area}")
def files_list(
    project_id: str,
    area: Area,
    path: str = Query(default=""),
    recursive: bool = Query(default=False),
    username: str = Depends(current_user),
):
    store = store_for(username, project_id)
    root = _safe_area_path(store, area, "")
    target = _safe_area_path(store, area, path)
    if not target.exists():
        raise HTTPException(404, "Path not found")
    if not target.is_dir():
        raise HTTPException(400, "Path is not a folder")
    iterator = target.rglob("*") if recursive else target.iterdir()
    return {"area": area, "path": path, "entries": [_entry(root, p) for p in sorted(iterator, key=lambda x: (not x.is_dir(), x.as_posix().lower()))]}


@app.get("/api/v1/projects/{project_id}/file/{area}")
def file_metadata(project_id: str, area: Area, path: str, username: str = Depends(current_user)):
    store = store_for(username, project_id)
    root = _safe_area_path(store, area, "")
    p = _safe_area_path(store, area, path)
    if not p.exists():
        raise HTTPException(404, "Path not found")
    return _entry(root, p)


@app.get("/api/v1/projects/{project_id}/file/{area}/text")
def file_read_text(project_id: str, area: Area, path: str, username: str = Depends(current_user)):
    p = _safe_area_path(store_for(username, project_id), area, path)
    if not p.is_file():
        raise HTTPException(404, "File not found")
    try:
        return {"path": path, "content": p.read_text(encoding="utf-8")}
    except UnicodeDecodeError as exc:
        raise HTTPException(415, "File is not UTF-8 text; use the download endpoint") from exc


@app.get("/api/v1/projects/{project_id}/file/{area}/download")
def file_download(project_id: str, area: Area, path: str, username: str = Depends(current_user)):
    p = _safe_area_path(store_for(username, project_id), area, path)
    if not p.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(p, filename=p.name)


@app.post("/api/v1/projects/{project_id}/file/{area}", status_code=201)
def file_create(project_id: str, area: Area, body: FileCreate, username: str = Depends(current_user)):
    p = _safe_area_path(store_for(username, project_id), area, body.path)
    if p.exists():
        raise HTTPException(409, "Destination already exists")
    if body.kind == "folder":
        p.mkdir(parents=True)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body.content, encoding="utf-8")
    return {"ok": True, "path": body.path, "type": body.kind}


@app.put("/api/v1/projects/{project_id}/file/{area}/text")
def file_write_text(project_id: str, area: Area, path: str, body: FileTextUpdate, username: str = Depends(current_user)):
    p = _safe_area_path(store_for(username, project_id), area, path)
    if p.exists() and not p.is_file():
        raise HTTPException(400, "Path is not a file")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.content, encoding="utf-8")
    return {"ok": True, "path": path}


@app.put("/api/v1/projects/{project_id}/file/{area}/binary")
async def file_write_binary(
    project_id: str,
    area: Area,
    path: str = Form(...),
    upload: UploadFile = File(...),
    username: str = Depends(current_user),
):
    p = _safe_area_path(store_for(username, project_id), area, path)
    if p.exists() and not p.is_file():
        raise HTTPException(400, "Path is not a file")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(await upload.read())
    return {"ok": True, "path": path, "filename": upload.filename}


@app.post("/api/v1/projects/{project_id}/files/{area}/upload")
async def files_upload(
    project_id: str,
    area: Area,
    folder: str = Form(default=""),
    uploads: list[UploadFile] = File(...),
    username: str = Depends(current_user),
):
    store = store_for(username, project_id)
    written = []
    for upload in uploads:
        name = Path(upload.filename or "upload.bin").name
        rel = (Path(folder) / name).as_posix() if folder else name
        p = _safe_area_path(store, area, rel)
        if p.exists():
            raise HTTPException(409, f"'{rel}' already exists")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(await upload.read())
        written.append(rel)
    return {"written": written}


@app.patch("/api/v1/projects/{project_id}/file/{area}")
def file_rename_move(project_id: str, area: Area, path: str, body: FileRenameMove, username: str = Depends(current_user)):
    store = store_for(username, project_id)
    src = _safe_area_path(store, area, path)
    dst = _safe_area_path(store, area, body.destination_path)
    if not src.exists():
        raise HTTPException(404, "Source path not found")
    if dst.exists():
        raise HTTPException(409, "Destination already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"ok": True, "path": body.destination_path}


@app.delete("/api/v1/projects/{project_id}/file/{area}")
def file_delete(project_id: str, area: Area, path: str, username: str = Depends(current_user)):
    p = _safe_area_path(store_for(username, project_id), area, path)
    if not p.exists():
        raise HTTPException(404, "Path not found")
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    return {"ok": True}


@app.post("/api/v1/projects/{project_id}/files/transfer")
def file_transfer(project_id: str, body: FileTransfer, username: str = Depends(current_user)):
    store = store_for(username, project_id)
    src = _safe_area_path(store, body.source_area, body.source_path)
    dst = _safe_area_path(store, body.destination_area, body.destination_path)
    if not src.exists():
        raise HTTPException(404, "Source path not found")
    if dst.exists():
        raise HTTPException(409, "Destination already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if body.operation == "move":
        shutil.move(str(src), str(dst))
    elif src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    return {"ok": True, "destination_area": body.destination_area, "destination_path": body.destination_path}


# ---------------------------------------------------------------------------
# Chat sessions + agent
# ---------------------------------------------------------------------------
@app.get("/api/v1/projects/{project_id}/sessions")
def sessions_list(project_id: str, username: str = Depends(current_user)):
    return {"sessions": ChatSessionStore(store_for(username, project_id)).list_sessions()}


@app.post("/api/v1/projects/{project_id}/sessions", status_code=201)
def session_create(project_id: str, body: SessionCreate, username: str = Depends(current_user)):
    return ChatSessionStore(store_for(username, project_id)).create(body.title)


@app.get("/api/v1/projects/{project_id}/sessions/{session_id}")
def session_get(project_id: str, session_id: str, username: str = Depends(current_user)):
    session = ChatSessionStore(store_for(username, project_id)).load(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    return session


@app.patch("/api/v1/projects/{project_id}/sessions/{session_id}")
def session_rename(project_id: str, session_id: str, body: SessionRename, username: str = Depends(current_user)):
    try:
        return ChatSessionStore(store_for(username, project_id)).rename(session_id, body.title)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Session not found") from exc


@app.delete("/api/v1/projects/{project_id}/sessions/{session_id}")
def session_delete(project_id: str, session_id: str, username: str = Depends(current_user)):
    ChatSessionStore(store_for(username, project_id)).delete(session_id)
    return {"ok": True}


@app.post("/api/v1/projects/{project_id}/query")
def query_agent(project_id: str, body: QueryRequest, username: str = Depends(current_user)):
    store = store_for(username, project_id)
    set_current_project(store)
    session_id = body.session_id or uuid.uuid4().hex
    chats = ChatSessionStore(store)
    stored = chats.load(session_id)
    transcript = list((stored or {}).get("messages", []))

    # Preserve order and only suppress an exact prefix already stored. Do not
    # set-deduplicate repeated user messages; repeated messages are legitimate.
    incoming = list(body.messages or [])
    if incoming:
        if len(incoming) >= len(transcript) and incoming[:len(transcript)] == transcript:
            transcript = incoming
        else:
            transcript.extend(incoming)
    try:
        answer, calls, drafts = run_agent(
            transcript,
            build_default_tools(username, store, True, session_id=session_id),
            store,
            project_id,
            session_id=session_id,
        )
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc

    user_message = next((m for m in reversed(transcript) if m.get("role") == "user"), {"role": "user", "content": ""})
    chats.append_turn(
        session_id,
        user_message,
        {"role": "assistant", "content": answer, "tool_calls": calls},
        title_from=str(user_message.get("content", "")),
    )
    return {"session_id": session_id, "answer": answer, "calls": calls, "drafts": drafts}


@app.get("/api/v1/projects/{project_id}/sessions/{session_id}/memory")
def session_memory(project_id: str, session_id: str, username: str = Depends(current_user)):
    return {"episodes": SessionMemory(store_for(username, project_id)).list(session_id)}


# ---------------------------------------------------------------------------
# Cognition
# ---------------------------------------------------------------------------
@app.get("/api/v1/projects/{project_id}/compile/files")
def compile_files(project_id: str, username: str = Depends(current_user)):
    return {"files": list_available_files(store_for(username, project_id))}


@app.post("/api/v1/projects/{project_id}/compile")
def compile_selected(project_id: str, body: CompileRequest, username: str = Depends(current_user)):
    store = store_for(username, project_id)
    try:
        return compile_project(store, selected_files=body.selected_files)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/cognition")
def cognition(project_id: str, q: str = "", username: str = Depends(current_user)):
    return _world_model_snapshot(store_for(username, project_id), q)


@app.get("/api/v1/projects/{project_id}/cognition/state")
def cognition_state(project_id: str, username: str = Depends(current_user)):
    return store_for(username, project_id).state_map()


@app.get("/api/v1/projects/{project_id}/cognition/ledger")
def cognition_ledger(project_id: str, username: str = Depends(current_user)):
    return store_for(username, project_id).ledger()


@app.get("/api/v1/projects/{project_id}/cognition/relationships")
def cognition_relationships(project_id: str, username: str = Depends(current_user)):
    return store_for(username, project_id).relationships()


@app.get("/api/v1/projects/{project_id}/cognition/validate")
def cognition_validate(project_id: str, username: str = Depends(current_user)):
    return _validate(store_for(username, project_id))


@app.get("/api/v1/projects/{project_id}/cognition/impact")
def cognition_impact(project_id: str, element: str, username: str = Depends(current_user)):
    return _impact(store_for(username, project_id), element)


# ---------------------------------------------------------------------------
# Draft review
# ---------------------------------------------------------------------------
@app.get("/api/v1/projects/{project_id}/drafts")
def drafts(project_id: str, status: str | None = None, username: str = Depends(current_user)):
    return {"drafts": DraftManager(store_for(username, project_id)).list(status=status)}


@app.put("/api/v1/projects/{project_id}/drafts/{draft_id}")
def draft_edit(project_id: str, draft_id: str, body: DraftEdit, username: str = Depends(current_user)):
    dm = DraftManager(store_for(username, project_id))
    try:
        draft = dm.load(draft_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Draft not found") from exc
    draft["content"] = body.content
    draft["metadata"] = body.metadata
    return dm.save(draft)


@app.post("/api/v1/projects/{project_id}/drafts/{draft_id}/approve")
def draft_approve(project_id: str, draft_id: str, username: str = Depends(current_user)):
    try:
        return ApprovalEngine(store_for(username, project_id)).approve(draft_id)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/v1/projects/{project_id}/drafts/{draft_id}/reject")
def draft_reject(project_id: str, draft_id: str, body: RejectRequest, username: str = Depends(current_user)):
    try:
        return ApprovalEngine(store_for(username, project_id)).reject(draft_id, body.reason)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------------------------------------------------------------------------
# Persistent instructions
# ---------------------------------------------------------------------------
@app.get("/api/v1/projects/{project_id}/instructions")
def instructions(project_id: str, status: str | None = None, username: str = Depends(current_user)):
    return {"instructions": InstructionStore(store_for(username, project_id)).list(status=status)}


@app.post("/api/v1/projects/{project_id}/instructions", status_code=201)
def instruction_add(project_id: str, body: InstructionCreate, username: str = Depends(current_user)):
    iid = InstructionStore(store_for(username, project_id)).add(body.content, body.scope, body.tagged_entity_id)
    return {"id": iid}


@app.put("/api/v1/projects/{project_id}/instructions/{instruction_id}")
def instruction_update(project_id: str, instruction_id: str, body: InstructionUpdate, username: str = Depends(current_user)):
    InstructionStore(store_for(username, project_id)).update(instruction_id, body.content)
    return {"ok": True}


@app.delete("/api/v1/projects/{project_id}/instructions/{instruction_id}")
def instruction_deactivate(project_id: str, instruction_id: str, username: str = Depends(current_user)):
    InstructionStore(store_for(username, project_id)).deactivate(instruction_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# History + settings
# ---------------------------------------------------------------------------
@app.get("/api/v1/projects/{project_id}/history")
def history(project_id: str, limit: int = Query(default=50, ge=1, le=500), username: str = Depends(current_user)):
    store = store_for(username, project_id)
    return {"log": VCSManager(store).log(limit=limit) if (store.root / ".git").exists() else ""}


@app.get("/api/v1/projects/{project_id}/settings")
def settings_get(project_id: str, username: str = Depends(current_user)):
    cfg = load_project_config(store_for(username, project_id))
    # Never return stored provider secrets to a frontend. Report presence only.
    public = dict(cfg)
    provider = dict(public.get("provider") or {})
    keys = provider.pop("api_keys", {}) or {}
    provider["api_key_configured"] = {k: bool(v) for k, v in keys.items()}
    public["provider"] = provider
    return public


@app.put("/api/v1/projects/{project_id}/settings")
def settings_put(project_id: str, body: ConfigUpdate, username: str = Depends(current_user)):
    store = store_for(username, project_id)
    existing = load_project_config(store)
    incoming = dict(body.config)
    # A GET->PUT round trip must not erase secrets because GET redacts them.
    old_provider = dict(existing.get("provider") or {})
    new_provider = dict(incoming.get("provider") or {})
    if "api_keys" not in new_provider:
        new_provider["api_keys"] = old_provider.get("api_keys", {})
    new_provider.pop("api_key_configured", None)
    incoming["provider"] = new_provider
    return save_project_config(store, incoming)


@app.put("/api/v1/projects/{project_id}/settings/provider-key")
def settings_provider_key(
    project_id: str,
    provider: str,
    api_key: str = Form(...),
    username: str = Depends(current_user),
):
    store = store_for(username, project_id)
    cfg = load_project_config(store)
    cfg.setdefault("provider", {}).setdefault("api_keys", {})[provider] = api_key
    save_project_config(store, cfg)
    return {"ok": True, "provider": provider}


# Small root document for humans; interactive schema is at /docs.
@app.get("/", response_class=PlainTextResponse)
def root():
    return f"Cognitive Persistence Agent API {API_VERSION}\nOpen /docs for the interactive API schema.\n"
