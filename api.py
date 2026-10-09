from __future__ import annotations

import base64
import json
import mimetypes
import os
import uuid
from pathlib import Path as FsPath
from typing import Any, Iterator

from fastapi import FastAPI, File, Form, HTTPException, Query as FastAPIQuery, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ragapp import __version__
from ragapp.auth.db import initialize_database
from ragapp.agent.loop import run_agent
from ragapp.agent.run_context import AgentRunContext
from ragapp.agent.trace import ExecutionTrace
from ragapp.cognition.compiler import compile_project, list_available_files
from ragapp.cognition.session_memory import SessionMemory
from ragapp.chat_sessions import ChatSessionStore
from ragapp.config import get_api_key, load_project_config, save_project_config
from ragapp.core.agents import AgentStore
from ragapp.core.approval import ApprovalEngine
from ragapp.core.drafts import DraftManager
from ragapp.core.instructions import InstructionStore
from ragapp.core.project_files import ProjectFileService
from ragapp.core.vcs import VCSManager
from ragapp.execution.project import (
    create_project,
    delete_project,
    get_project,
    list_projects,
    rename_project,
)
from ragapp.llm.registry import PROVIDER_SPECS, limit
from ragapp.tools import build_default_tools
from ragapp.tools.cognition_tools import _impact, _validate, _world_model_snapshot
from ragapp.workspace.manager import set_current_project

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Cognitive Persistence Agent API",
    version=__version__,
    description=(
        "HTTP API for the same project, cognition, agent, chat-session, "
        "draft, file, VCS, instruction and settings capabilities exposed "
        "by the Streamlit Cognitive Persistence Agent UI."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

initialize_database()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class Query(BaseModel):
    username: str
    project_id: str
    messages: list[dict] = Field(default_factory=list)
    session_id: str | None = None
    agent_id: str | None = None


class ChatTurn(BaseModel):
    username: str
    project_id: str
    session_id: str
    text: str
    parent_id: str | None = None
    replacing_turn_id: str | None = None
    agent_id: str | None = None


class ProjectCreate(BaseModel):
    username: str
    project_id: str


class ProjectRename(BaseModel):
    username: str
    project_id: str
    new_name: str


class ProjectDelete(BaseModel):
    username: str
    project_id: str


class SessionCreate(BaseModel):
    title: str | None = None


class SessionRename(BaseModel):
    title: str


class SessionEdit(BaseModel):
    turn_id: str
    content: str


class SessionActivate(BaseModel):
    turn_id: str


class Reject(BaseModel):
    reason: str = ""


class DraftEdit(BaseModel):
    content: str
    metadata: dict = Field(default_factory=dict)


class Instruction(BaseModel):
    content: str
    scope: str = "situational"
    tagged_entity_id: str | None = None


class InstructionUpdate(BaseModel):
    content: str


class CompileRequest(BaseModel):
    files: list[tuple[str, str]] = Field(default_factory=list)


class FileWrite(BaseModel):
    content: str
    overwrite: bool = False
    message: str | None = None


class FileMove(BaseModel):
    source_area: str
    source_path: str
    destination_area: str
    destination_path: str
    message: str | None = None


class FileRestore(BaseModel):
    commit: str
    message: str | None = None


class Checkpoint(BaseModel):
    message: str = "Manual project checkpoint"


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
    workflow_steps: list[dict] = Field(default_factory=list)
    trigger: dict = Field(default_factory=lambda: {"type": "manual"})
    require_mutation_approval: bool = True
    enabled: bool = True


class SettingsUpdate(BaseModel):
    provider: dict = Field(default_factory=dict)
    limits: dict = Field(default_factory=dict)
    features: dict = Field(default_factory=dict)
    api_key: str | None = None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def store_for(username: str, project_id: str):
    try:
        return get_project(username, project_id)
    except Exception as exc:
        raise HTTPException(404, f"Project '{project_id}' was not found: {exc}")


def chats_for(username: str, project_id: str) -> ChatSessionStore:
    return ChatSessionStore(store_for(username, project_id))


def _serialise_tool_calls(calls: Any) -> list:
    if calls is None:
        return []
    result = []
    for call in calls:
        if isinstance(call, dict):
            result.append(call)
        else:
            try:
                result.append(
                    {
                        "tool": getattr(call, "tool", getattr(call, "name", str(call))),
                        "args": getattr(call, "args", {}),
                        "result": getattr(call, "result", None),
                    }
                )
            except Exception:
                result.append({"tool": str(call)})
    return result


def _project_summary(store) -> dict:
    state = store.state_map()
    counts = store.cognition_counts()
    return {
        "project_id": store.project_id,
        "counts": counts,
        "cognition_version": state.get("current_version", 0),
        "compiled": bool(state.get("compiled")),
        "source_file_count": sum(p.is_file() for p in store.source.rglob("*")),
        "workspace_file_count": sum(p.is_file() for p in store.workspace.rglob("*")),
        "config": load_project_config(store),
    }


def _session_active_drafts(store, session_id: str) -> list[str]:
    chats = ChatSessionStore(store)
    data = chats.load(session_id)
    if not data:
        return []
    ids: list[str] = []
    for tid in chats.lineage_ids_data(data):
        ids.extend(
            ((data.get("turns") or {}).get(tid, {}).get("effects") or {}).get("draft_ids") or []
        )
    return ids


def _activate_turn(store, session_id: str, turn_id: str) -> dict:
    chats = ChatSessionStore(store)
    data = chats.load(session_id)
    if not data or turn_id not in data.get("turns", {}):
        raise HTTPException(404, "turn not found")
    turn = data["turns"][turn_id]
    target = turn.get("state_after") or turn.get("state_before")
    if target:
        VCSManager(store).materialize_state(
            target,
            message=f"Activate chat branch {turn_id[:8]}",
        )
    refreshed = chats.set_active_leaf(session_id, turn_id)
    DraftManager(store).set_session_active_drafts(
        session_id,
        _session_active_drafts(store, session_id),
    )
    return refreshed


def _execute_chat_turn(
    username: str,
    project_id: str,
    session_id: str,
    user_text: str,
    *,
    parent_id: str | None = None,
    replacing_turn_id: str | None = None,
    agent_id: str | None = None,
    on_section=None,
) -> dict:
    store = store_for(username, project_id)
    set_current_project(store)
    chats = ChatSessionStore(store)
    current = chats.load(session_id)
    if current is None:
        current = chats.create(session_id=session_id) if hasattr(chats, "create") else chats.create()
        session_id = current["id"]

    turns = current.get("turns") or {}

    if replacing_turn_id:
        old = turns.get(replacing_turn_id)
        if not old:
            raise HTTPException(404, "turn to replace not found")
        parent_id = old.get("parent_id")
        if old.get("state_before"):
            VCSManager(store).materialize_state(
                old["state_before"],
                message=f"Fork chat before {replacing_turn_id[:8]}",
            )
    elif parent_id is None:
        parent_id = current.get("active_leaf_id")

    vcs = VCSManager(store)
    state_before = vcs.checkpoint(
        f"Before chat turn: {user_text[:48]}"
    )
    turn_id = uuid.uuid4().hex

    base_transcript = (
        chats.active_transcript_data(current, parent_id)
        if parent_id
        else []
    )
    user_message = {
        "role": "user",
        "content": user_text,
        "turn_id": turn_id,
    }
    transcript = base_transcript + [user_message]

    active_ids = (
        chats.lineage_ids_data(current, parent_id)
        if parent_id
        else []
    )
    active_ids = active_ids + [turn_id]

    execution_trace = ExecutionTrace(enabled=True)
    calls = []
    drafts = []
    answer = ""

    try:
        answer, calls, drafts = run_agent(
            AgentRunContext(
                transcript=transcript,
                tools=build_default_tools(
                    username,
                    store,
                    include_cognition=True,
                    session_id=session_id,
                ),
                cognition=store,
                project_id=project_id,
                session_id=session_id,
                turn_id=turn_id,
                active_turn_ids=active_ids,
                on_section=on_section,
                agent_id=agent_id,
                trace=execution_trace,
            )
        )
    except Exception as exc:
        raise HTTPException(500, str(exc))

    answer = (
        (answer or "").strip()
        or "⚠️ The agent produced no response for this turn. Try again."
    )
    tool_calls = _serialise_tool_calls(calls)
    state_after = vcs.checkpoint(
        f"After chat turn: {user_text[:48]}"
    )

    assistant_message = {
        "role": "assistant",
        "content": answer,
        "tool_calls": tool_calls,
        "turn_id": turn_id,
        "execution_trace": execution_trace.as_dict(),
    }

    effects = {
        "draft_ids": [
            d.get("id")
            for d in drafts
            if isinstance(d, dict)
        ],
        "tool_count": len(calls),
    }

    if replacing_turn_id:
        refreshed = chats.fork_turn(
            session_id,
            replacing_turn_id,
            user_message,
            assistant_message,
            state_before=state_before,
            state_after=state_after,
            effects=effects,
            new_turn_id=turn_id,
            title_from=user_text,
        )
    else:
        refreshed = chats.append_turn(
            session_id,
            user_message,
            assistant_message,
            title_from=user_text,
            parent_id=parent_id,
            state_before=state_before,
            state_after=state_after,
            effects=effects,
            turn_id=turn_id,
        )

    active_drafts = _session_active_drafts(store, session_id)
    DraftManager(store).set_session_active_drafts(
        session_id,
        active_drafts,
    )

    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "parent_id": parent_id,
        "answer": answer,
        "calls": tool_calls,
        "drafts": drafts,
        "execution_trace": execution_trace.as_dict(),
        "session": refreshed,
        "state_before": state_before,
        "state_after": state_after,
    }


def _safe_project_file(store, area: str, path: str) -> FsPath:
    if area not in {"source", "workspace"}:
        raise HTTPException(400, "area must be 'source' or 'workspace'")
    root = (store.source if area == "source" else store.workspace).resolve()
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(400, "path escapes project area")
    return candidate


# ---------------------------------------------------------------------------
# Health / project management
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/providers")
def providers():
    return {
        key: {
            "id": key,
            "label": spec.get("label", key),
            "default_model": spec.get("default_model"),
            "model_label": spec.get("model_label"),
            "key_label": spec.get("key_label"),
            "key_slot": spec.get("key_slot"),
            "endpoint_env": spec.get("endpoint_env"),
        }
        for key, spec in PROVIDER_SPECS.items()
    }


@app.get("/projects/{username}")
def projects(username: str):
    # Keep the original API contract: this endpoint returns the project-name list.
    return list_projects(username)


@app.get("/projects/{username}/details")
def project_details(username: str):
    names = list_projects(username)
    return {
        "projects": names,
        "items": [
            _project_summary(get_project(username, name))
            for name in names
        ],
    }


@app.post("/projects")
def project(p: ProjectCreate):
    return {
        "project_id": create_project(p.username, p.project_id).project_id
    }


@app.get("/projects/{username}/{project_id}")
def project_detail(username: str, project_id: str):
    return _project_summary(store_for(username, project_id))


@app.put("/projects/{username}/{project_id}")
def project_rename(username: str, project_id: str, body: ProjectRename):
    if body.username != username:
        raise HTTPException(400, "username mismatch")
    try:
        renamed = rename_project(username, project_id, body.new_name)
        return {
            "project_id": renamed.project_id,
            "project": _project_summary(renamed),
        }
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.delete("/projects/{username}/{project_id}")
def project_delete(username: str, project_id: str):
    try:
        delete_project(username, project_id)
        remaining = list_projects(username)
        return {"deleted": project_id, "projects": remaining}
    except Exception as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

@app.post("/query")
def query(q: Query):
    if not q.messages:
        raise HTTPException(400, "messages must contain at least one message")
    user_message = next(
        (
            m for m in reversed(q.messages)
            if m.get("role") == "user"
        ),
        None,
    )
    if not user_message:
        raise HTTPException(400, "messages must contain a user message")

    store = store_for(q.username, q.project_id)
    chats = ChatSessionStore(store)

    session_id = q.session_id
    if not session_id:
        session = chats.create()
        session_id = session["id"]
    else:
        session = chats.load(session_id)
        if not session:
            raise HTTPException(404, "session not found")

    # Preserve compatibility with the original API: accept a complete
    # transcript, but execute only the newest user message.
    result = _execute_chat_turn(
        q.username,
        q.project_id,
        session_id,
        str(user_message.get("content", "")),
        agent_id=q.agent_id,
    )
    return result


@app.post("/query/turn")
def query_turn(q: ChatTurn):
    return _execute_chat_turn(
        q.username,
        q.project_id,
        q.session_id,
        q.text,
        parent_id=q.parent_id,
        replacing_turn_id=q.replacing_turn_id,
        agent_id=q.agent_id,
    )


@app.post("/query/stream")
def query_stream(q: ChatTurn):
    """
    Server-Sent Events endpoint.

    Events:
      plan       -> {"sections": [...]}
      section    -> {"title": "...", "content": "..."}
      complete   -> final turn payload
      error      -> {"detail": "..."}
    """
    import queue
    import threading

    events: queue.Queue = queue.Queue()
    sentinel = object()

    def on_section(event: dict):
        events.put(("section", event))

    def worker():
        try:
            result = _execute_chat_turn(
                q.username,
                q.project_id,
                q.session_id,
                q.text,
                parent_id=q.parent_id,
                replacing_turn_id=q.replacing_turn_id,
                agent_id=q.agent_id,
                on_section=on_section,
            )
            events.put(("complete", result))
        except Exception as exc:
            events.put(("error", {"detail": str(exc)}))
        finally:
            events.put(("done", sentinel))

    threading.Thread(target=worker, daemon=True).start()

    def event_stream() -> Iterator[str]:
        while True:
            kind, payload = events.get()
            if kind == "done":
                break
            yield (
                f"event: {kind}\n"
                f"data: {json.dumps(payload, default=str)}\n\n"
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Chat sessions: create, list, inspect, rename, delete, branch, activate
# ---------------------------------------------------------------------------

@app.get("/sessions/{username}/{project_id}")
def sessions(username: str, project_id: str):
    return ChatSessionStore(store_for(username, project_id)).list_sessions()


@app.post("/sessions/{username}/{project_id}")
def session_create(username: str, project_id: str, body: SessionCreate | None = None):
    chats = ChatSessionStore(store_for(username, project_id))
    data = chats.create()
    if body and body.title:
        try:
            chats.rename(data["id"], body.title)
            data = chats.load(data["id"]) or data
        except Exception:
            pass
    return data


@app.get("/sessions/{username}/{project_id}/{session_id}")
def session_detail(username: str, project_id: str, session_id: str):
    data = ChatSessionStore(store_for(username, project_id)).load(session_id)
    if not data:
        raise HTTPException(404, "session not found")
    return data


@app.patch("/sessions/{username}/{project_id}/{session_id}")
def session_rename(username: str, project_id: str, session_id: str, body: SessionRename):
    chats = ChatSessionStore(store_for(username, project_id))
    if not chats.load(session_id):
        raise HTTPException(404, "session not found")
    return chats.rename(session_id, body.title)


@app.delete("/sessions/{username}/{project_id}/{session_id}")
def session_delete(username: str, project_id: str, session_id: str):
    chats = ChatSessionStore(store_for(username, project_id))
    if not chats.load(session_id):
        raise HTTPException(404, "session not found")
    chats.delete(session_id)
    remaining = chats.list_sessions()
    if not remaining:
        new_session = chats.create()
        remaining = [new_session]
    return {"deleted": session_id, "sessions": remaining}


@app.get("/sessions/{username}/{project_id}/{session_id}/memory")
def session_memory(username: str, project_id: str, session_id: str):
    return SessionMemory(
        store_for(username, project_id)
    ).list(session_id)


@app.post("/sessions/{username}/{project_id}/{session_id}/activate")
def session_activate(
    username: str,
    project_id: str,
    session_id: str,
    body: SessionActivate,
):
    return _activate_turn(
        store_for(username, project_id),
        session_id,
        body.turn_id,
    )


@app.post("/sessions/{username}/{project_id}/{session_id}/edit")
def session_edit(
    username: str,
    project_id: str,
    session_id: str,
    body: SessionEdit,
):
    store = store_for(username, project_id)
    data = ChatSessionStore(store).load(session_id)
    if not data or body.turn_id not in data.get("turns", {}):
        raise HTTPException(404, "turn not found")

    return _execute_chat_turn(
        username,
        project_id,
        session_id,
        body.content,
        replacing_turn_id=body.turn_id,
    )


# ---------------------------------------------------------------------------
# Draft review
# ---------------------------------------------------------------------------

@app.get("/drafts/{username}/{project_id}")
def drafts(username: str, project_id: str):
    return DraftManager(store_for(username, project_id)).list()


@app.get("/drafts/{username}/{project_id}/{draft_id}")
def draft_detail(username: str, project_id: str, draft_id: str):
    dm = DraftManager(store_for(username, project_id))
    draft = dm.load(draft_id)
    if not draft:
        raise HTTPException(404, "draft not found")
    return draft


@app.put("/drafts/{username}/{project_id}/{draft_id}")
def edit_draft(
    username: str,
    project_id: str,
    draft_id: str,
    body: DraftEdit,
):
    dm = DraftManager(store_for(username, project_id))
    draft = dm.load(draft_id)
    if not draft:
        raise HTTPException(404, "draft not found")
    draft["content"] = body.content
    draft["metadata"] = body.metadata
    return dm.save(draft)


@app.post("/drafts/{username}/{project_id}/{draft_id}/approve")
def approve(username: str, project_id: str, draft_id: str):
    try:
        return ApprovalEngine(
            store_for(username, project_id)
        ).approve(draft_id)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/drafts/{username}/{project_id}/{draft_id}/reject")
def reject(
    username: str,
    project_id: str,
    draft_id: str,
    body: Reject,
):
    try:
        return ApprovalEngine(
            store_for(username, project_id)
        ).reject(draft_id, body.reason)
    except Exception as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------------------
# Persistent instructions
# ---------------------------------------------------------------------------

@app.get("/instructions/{username}/{project_id}")
def instructions(username: str, project_id: str):
    return InstructionStore(
        store_for(username, project_id)
    ).list()


@app.post("/instructions/{username}/{project_id}")
def add_instruction(
    username: str,
    project_id: str,
    body: Instruction,
):
    try:
        iid = InstructionStore(
            store_for(username, project_id)
        ).add(
            body.content,
            body.scope,
            body.tagged_entity_id,
            "user_stated",
        )
        return {"id": iid}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.patch("/instructions/{username}/{project_id}/{iid}")
def patch_instruction(
    username: str,
    project_id: str,
    iid: str,
    body: InstructionUpdate | None = None,
):
    try:
        store = store_for(username, project_id)
        ins = InstructionStore(store)
        if body is None:
            ins.deactivate(iid)
            return {"ok": True, "id": iid, "status": "deactivated"}
        ins.update(iid, body.content)
        return {"ok": True, "id": iid, "status": "updated"}
    except Exception as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------------------
# Cognition
# ---------------------------------------------------------------------------

@app.get("/cognition/{username}/{project_id}")
def cognition(
    username: str,
    project_id: str,
    query: str = "",
):
    return _world_model_snapshot(
        store_for(username, project_id),
        query,
    )


@app.get("/cognition/{username}/{project_id}/overview")
def cognition_overview(username: str, project_id: str):
    store = store_for(username, project_id)
    return {
        "snapshot": _world_model_snapshot(store, ""),
        "state": store.state_map(),
        "counts": store.cognition_counts(),
        "ledger": store.ledger(),
        "relationships": store.relationships(),
        "available_files": list_available_files(store),
    }


@app.get("/cognition/{username}/{project_id}/counts")
def cognition_counts(username: str, project_id: str):
    store = store_for(username, project_id)
    return {
        "counts": store.cognition_counts(),
        "version": store.state_map().get("current_version", 0),
        "compiled": bool(store.state_map().get("compiled")),
    }


@app.get("/cognition/{username}/{project_id}/state")
def cognition_state(username: str, project_id: str):
    return store_for(username, project_id).state_map()


@app.get("/cognition/{username}/{project_id}/ledger")
def cognition_ledger(username: str, project_id: str):
    return store_for(username, project_id).ledger()


@app.get("/cognition/{username}/{project_id}/relationships")
def cognition_relationships(username: str, project_id: str):
    return store_for(username, project_id).relationships()


@app.get("/cognition/{username}/{project_id}/validate")
def cognition_validate(username: str, project_id: str):
    return _validate(store_for(username, project_id))


@app.get("/cognition/{username}/{project_id}/impact")
def cognition_impact(
    username: str,
    project_id: str,
    element: str,
):
    return _impact(store_for(username, project_id), element)


@app.get("/cognition/{username}/{project_id}/files")
def cognition_files(username: str, project_id: str):
    return list_available_files(store_for(username, project_id))


@app.post("/cognition/{username}/{project_id}/compile")
def cognition_compile(
    username: str,
    project_id: str,
    body: CompileRequest,
):
    store = store_for(username, project_id)
    try:
        selected = body.files or None
        result = compile_project(
            store,
            selected_files=selected,
        )
        return {
            "result": result,
            "counts": store.cognition_counts(),
            "state": store.state_map(),
        }
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/cognition/{username}/{project_id}/compile/stream")
def cognition_compile_stream(
    username: str,
    project_id: str,
    body: CompileRequest,
):
    """
    Server-Sent Events compile, mirroring the live progress the Streamlit UI shows.

    Events:
      start     -> {"counts": {...}, "state": {...}}
      progress  -> {"done": n, "total": m, "counts": {...}, "state": {...}}
      complete  -> {"result": {...}, "counts": {...}, "state": {...}}
      error     -> {"detail": "..."}
    """
    import queue
    import threading

    store = store_for(username, project_id)
    events: queue.Queue = queue.Queue()

    def snapshot() -> dict:
        return {"counts": store.cognition_counts(), "state": store.state_map()}

    def on_progress(done, total):
        payload = {"done": done, "total": total}
        try:
            payload.update(snapshot())
        except Exception:
            pass
        events.put(("progress", payload))

    def worker():
        try:
            events.put(("start", snapshot()))
            result = compile_project(
                store,
                selected_files=body.files or None,
                progress_callback=on_progress,
            )
            events.put(("complete", {"result": result, **snapshot()}))
        except Exception as exc:
            events.put(("error", {"detail": str(exc)}))
        finally:
            events.put(("done", None))

    threading.Thread(target=worker, daemon=True).start()

    def event_stream() -> Iterator[str]:
        while True:
            kind, payload = events.get()
            if kind == "done":
                break
            yield f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Preserve the original ingest endpoint.
@app.post("/ingest/{username}/{project_id}")
def ingest(username: str, project_id: str):
    return compile_project(store_for(username, project_id))


# ---------------------------------------------------------------------------
# File manager / preview
# ---------------------------------------------------------------------------

@app.get("/projects/{username}/{project_id}/files")
def files(
    username: str,
    project_id: str,
    area: str | None = None,
):
    store = store_for(username, project_id)
    areas = [area] if area else ["source", "workspace"]
    output = []

    for current_area in areas:
        if current_area not in {"source", "workspace"}:
            raise HTTPException(400, "area must be 'source' or 'workspace'")
        root = store.source if current_area == "source" else store.workspace
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = p.relative_to(root).as_posix()
                stat = p.stat()
                output.append(
                    {
                        "area": current_area,
                        "path": rel,
                        "name": p.name,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                        "suffix": p.suffix.lower(),
                        "mime": mimetypes.guess_type(p.name)[0] or "application/octet-stream",
                    }
                )
    return output


@app.get("/projects/{username}/{project_id}/files/{area}/text")
def file_text(
    username: str,
    project_id: str,
    area: str,
    path: str,
):
    store = store_for(username, project_id)
    target = _safe_project_file(store, area, path)
    if not target.is_file():
        raise HTTPException(404, "file not found")
    try:
        return {
            "area": area,
            "path": path,
            "content": target.read_text(encoding="utf-8"),
        }
    except UnicodeDecodeError:
        raise HTTPException(415, "file is not UTF-8 text")


@app.get("/projects/{username}/{project_id}/files/{area}/download")
def file_download(
    username: str,
    project_id: str,
    area: str,
    path: str,
):
    store = store_for(username, project_id)
    target = _safe_project_file(store, area, path)
    if not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(
        target,
        filename=target.name,
        media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream",
    )


MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024


@app.post("/projects/{username}/{project_id}/files/{area}/upload")
async def file_upload(
    username: str,
    project_id: str,
    area: str,
    files: list[UploadFile] = File(...),
    folder: str = Form(""),
    overwrite: bool = Form(False),
):
    """Upload one or more files into `area`/`folder` as a single recoverable commit."""
    prefix = folder.replace("\\", "/").strip().strip("/")
    batch, total = [], 0
    for up in files:
        name = FsPath(up.filename or "").name  # a client-supplied filename is never trusted as a path
        if not name:
            raise HTTPException(400, "An uploaded file has no name.")
        if up.size and total + up.size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"Upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        data = await up.read()
        total += len(data)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"Upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        batch.append((f"{prefix}/{name}" if prefix else name, data))
    try:
        service = ProjectFileService(store_for(username, project_id))
        result = await run_in_threadpool(
            service.write_many, area, batch,
            overwrite=overwrite, description=f"Upload {len(batch)} file(s) to {area}",
        )
        return {**result, "files": [p for p, _ in batch]}
    except FileExistsError as exc:
        raise HTTPException(409, f"File already exists: {exc}. Enable overwrite to replace it.")
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/projects/{username}/{project_id}/files/{area}/raw/{file_path:path}")
def file_raw(username: str, project_id: str, area: str, file_path: str):
    """
    Serve a project file inline with its real MIME type.

    The path is part of the URL (not a query parameter) so relative references inside an HTML
    file (./style.css, ../img/logo.png) resolve to sibling files through this same route. Active
    content (html/svg) is served with a CSP sandbox: scripts may run, but in an opaque origin
    with no access to the app, cookies or storage.
    """
    store = store_for(username, project_id)
    target = _safe_project_file(store, area, file_path)
    if not target.is_file():
        raise HTTPException(404, "file not found")
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"}
    if target.suffix.lower() in {".html", ".htm", ".svg", ".xml"}:
        headers["Content-Security-Policy"] = "sandbox allow-scripts allow-popups allow-forms"
    return FileResponse(target, media_type=mime, headers=headers)


@app.get("/projects/{username}/{project_id}/files/{area}/preview")
def file_preview(
    username: str,
    project_id: str,
    area: str,
    path: str,
):
    store = store_for(username, project_id)
    target = _safe_project_file(store, area, path)
    if not target.is_file():
        raise HTTPException(404, "file not found")

    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"

    if mime.startswith("text/") or target.suffix.lower() in {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
        ".toml", ".ini", ".cfg", ".md", ".sql", ".sh", ".ksh", ".xml",
        ".html", ".css", ".csv", ".txt",
    }:
        try:
            return {
                "kind": "text",
                "area": area,
                "path": path,
                "mime": mime,
                "content": target.read_text(encoding="utf-8"),
            }
        except UnicodeDecodeError:
            pass

    return {
        "kind": "binary",
        "area": area,
        "path": path,
        "mime": mime,
        "download_url": (
            f"/projects/{username}/{project_id}/files/"
            f"{area}/download?path={path}"
        ),
    }


@app.get("/projects/{username}/{project_id}/files/{area}/history")
def file_history(
    username: str,
    project_id: str,
    area: str,
    path: str,
    limit: int = 50,
):
    try:
        return ProjectFileService(
            store_for(username, project_id)
        ).history(
            area,
            path,
            min(max(limit, 1), 100),
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/projects/{username}/{project_id}/files/{area}/revision")
def file_revision(
    username: str,
    project_id: str,
    area: str,
    path: str,
    commit: str,
):
    try:
        content = ProjectFileService(
            store_for(username, project_id)
        ).show_revision(area, path, commit)
        return {
            "area": area,
            "path": path,
            "commit": commit,
            "content": content,
        }
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/projects/{username}/{project_id}/files/{area}/diff")
def file_diff(
    username: str,
    project_id: str,
    area: str,
    path: str,
    revision_a: str,
    revision_b: str,
):
    try:
        vcs = VCSManager(store_for(username, project_id))
        return {
            "diff": vcs.diff(
                revision_a,
                revision_b,
                f"{area}/{path}",
            )
        }
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/projects/{username}/{project_id}/files/{area}/restore")
def file_restore(
    username: str,
    project_id: str,
    area: str,
    path: str,
    body: FileRestore,
):
    try:
        return ProjectFileService(
            store_for(username, project_id)
        ).restore(
            area,
            path,
            body.commit,
            body.message,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.put("/projects/{username}/{project_id}/files/{area}/text")
def file_write(
    username: str,
    project_id: str,
    area: str,
    path: str,
    body: FileWrite,
):
    try:
        return ProjectFileService(
            store_for(username, project_id)
        ).write_text(
            area,
            path,
            body.content,
            overwrite=body.overwrite,
            description=body.message,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.delete("/projects/{username}/{project_id}/files/{area}")
def file_delete(
    username: str,
    project_id: str,
    area: str,
    path: str,
):
    try:
        return ProjectFileService(
            store_for(username, project_id)
        ).delete(area, path)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/projects/{username}/{project_id}/files/move")
def file_move(
    username: str,
    project_id: str,
    body: FileMove,
):
    try:
        return ProjectFileService(
            store_for(username, project_id)
        ).move(
            body.source_area,
            body.source_path,
            body.destination_area,
            body.destination_path,
            body.message,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/projects/{username}/{project_id}/files/copy")
def file_copy(
    username: str,
    project_id: str,
    body: FileMove,
):
    try:
        return ProjectFileService(
            store_for(username, project_id)
        ).copy(
            body.source_area,
            body.source_path,
            body.destination_area,
            body.destination_path,
            body.message,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/projects/{username}/{project_id}/vcs/checkpoint")
def vcs_checkpoint(
    username: str,
    project_id: str,
    body: Checkpoint,
):
    try:
        return {
            "commit": VCSManager(
                store_for(username, project_id)
            ).checkpoint(body.message)
        }
    except Exception as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------------------
# Git / history
# ---------------------------------------------------------------------------

@app.get("/git/{username}/{project_id}/log")
def gitlog(username: str, project_id: str):
    store = store_for(username, project_id)
    return {
        "log": (
            VCSManager(store).log()
            if (store.root / ".git").exists()
            else ""
        )
    }


@app.get("/history/{username}/{project_id}")
def history(username: str, project_id: str):
    store = store_for(username, project_id)
    return {
        "log": (
            VCSManager(store).log()
            if (store.root / ".git").exists()
            else ""
        )
    }


# ---------------------------------------------------------------------------
# Persistent agents
# ---------------------------------------------------------------------------

@app.get("/agents/{username}/{project_id}")
def list_agent_specs(username: str, project_id: str):
    return AgentStore(store_for(username, project_id)).list()


@app.get("/agents/{username}/{project_id}/{agent_id}")
def get_agent_spec(
    username: str,
    project_id: str,
    agent_id: str,
):
    value = AgentStore(
        store_for(username, project_id)
    ).get(agent_id)
    if not value:
        raise HTTPException(404, "Agent not found")
    return value


@app.put("/agents/{username}/{project_id}")
def put_agent_spec(
    username: str,
    project_id: str,
    body: AgentSpec,
):
    try:
        return AgentStore(
            store_for(username, project_id)
        ).save(body.model_dump())
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.delete("/agents/{username}/{project_id}/{agent_id}")
def delete_agent_spec(
    username: str,
    project_id: str,
    agent_id: str,
):
    try:
        return {
            "deleted": AgentStore(
                store_for(username, project_id)
            ).delete(agent_id)
        }
    except Exception as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------------------
# Project settings
# ---------------------------------------------------------------------------

@app.get("/settings/{username}/{project_id}")
def settings(username: str, project_id: str):
    store = store_for(username, project_id)
    cfg = load_project_config(store)
    return {
        "config": cfg,
        "provider_specs": providers(),
        "limits": {
            key: limit(cfg, key)
            for key in (
                "max_agent_steps",
                "transcript_turns",
                "transcript_chars",
                "requests_per_minute",
            )
        },
    }


@app.put("/settings/{username}/{project_id}")
def update_settings(
    username: str,
    project_id: str,
    body: SettingsUpdate,
):
    store = store_for(username, project_id)
    cfg = load_project_config(store)

    provider = cfg.setdefault("provider", {})
    limits_cfg = cfg.setdefault("limits", {})
    features = cfg.setdefault("features", {})

    provider.update(body.provider or {})
    limits_cfg.update(body.limits or {})
    features.update(body.features or {})

    if body.api_key:
        provider.setdefault("api_keys", {})
        key_slot = provider.get("key_slot")
        provider_spec = PROVIDER_SPECS.get(provider.get("name", ""))
        if provider_spec:
            key_slot = provider_spec.get("key_slot", key_slot)
        if key_slot:
            provider["api_keys"][key_slot] = body.api_key

    cfg["provider"] = provider
    cfg["limits"] = limits_cfg
    cfg["features"] = features

    save_project_config(store, cfg)
    return {
        "ok": True,
        "config": load_project_config(store),
    }


# ---------------------------------------------------------------------------
# Compatibility aliases
# ---------------------------------------------------------------------------

@app.get("/projects/{username}/{project_id}/status")
def project_status(username: str, project_id: str):
    return _project_summary(store_for(username, project_id))


@app.get("/projects/{username}/{project_id}/available-files")
def available_files(username: str, project_id: str):
    return list_available_files(store_for(username, project_id))