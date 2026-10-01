from pathlib import Path
import base64
from io import BytesIO
import json
import os
import signal
import threading
import time
import uuid

import streamlit as st
from ragapp.auth.db import initialize_database
from ragapp.interfaces.streamlit_app.auth_ui import login, logout
from ragapp.cognition.compiler import compile_project, list_available_files
from ragapp.execution.project import (
    get_project,
    list_projects,
    create_project,
    delete_project,
    rename_project,
)
from ragapp.workspace.manager import set_current_project
from ragapp.agent.loop import run_agent
from ragapp.agent.run_context import AgentRunContext
from ragapp.agent.trace import ExecutionTrace
from ragapp.tools import build_default_tools
from ragapp.chat_sessions import ChatSessionStore
from ragapp.interfaces.streamlit_app.file_manager import (
    render_file_manager,
    _prepare_html_preview,
)
from ragapp.interfaces.streamlit_app.previews import render_document_preview, DOCUMENT_SUFFIXES
from ragapp.core.drafts import DraftManager
from ragapp.core.agents import AgentStore
from ragapp.core.approval import ApprovalEngine
from ragapp.core.instructions import InstructionStore
from ragapp.core.vcs import VCSManager
from ragapp.llm.registry import PROVIDER_SPECS, limit
from ragapp.config import (
    load_project_config,
    save_project_config,
    get_api_key,
)

# ===========================================================================
# Page configuration
# ===========================================================================

st.set_page_config(
    page_title="Cognitive Persistence Agent",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
.block-container {
    padding-top: 1.1rem;
    padding-bottom: 1rem;
    max-width: 1500px;
}

[data-testid="stAppViewContainer"] {
    overflow: hidden;
}

.small-muted {
    color: #888;
    font-size: .85rem;
}

.main .block-container {
    overflow: hidden;
}

[data-testid="stSidebar"] { min-width: 250px; max-width: 280px; }
[data-testid="stSidebar"] .block-container { padding-top: .55rem; padding-bottom: .55rem; }
[data-testid="stSidebar"] h1 { font-size: 1.25rem; margin-bottom: .1rem; }
[data-testid="stSidebar"] h3 { font-size: .92rem; margin: .35rem 0 .15rem; }
[data-testid="stSidebar"] hr { margin: .35rem 0; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: .35rem; }
[data-testid="stSidebar"] button { min-height: 2rem; }
[data-testid="stChatInput"] {
    position: static !important;
    bottom: auto !important;
    left: auto !important;
    right: auto !important;
    width: 100% !important;
    margin-top: 0.75rem;
    z-index: auto !important;
}
</style>
""",
    unsafe_allow_html=True,
)

# ===========================================================================
# Application bootstrap
# ===========================================================================

initialize_database()

st.session_state.setdefault(
    "authenticated",
    False,
)

if not login():
    st.stop()

username = st.session_state.username

st.session_state.setdefault(
    "project_id",
    "default",
)

# ===========================================================================
# Browser/session + server lifecycle helpers
# ===========================================================================

def _query_value(name: str) -> str | None:
    """Return one non-empty query-parameter value."""
    try:
        value = st.query_params.get(name)
    except Exception:
        return None
    if isinstance(value, list):
        value = value[-1] if value else None
    value = str(value).strip() if value is not None else ""
    return value or None


def _sync_browser_location(project_id: str, session_id: str | None = None) -> None:
    """Persist non-secret navigation state across a browser refresh."""
    try:
        st.query_params["project"] = project_id
        if session_id:
            st.query_params["chat"] = session_id
        elif "chat" in st.query_params:
            del st.query_params["chat"]
    except Exception:
        # Navigation persistence is helpful but must never break the UI.
        pass


def _terminate_streamlit_process_after_response(delay: float = 0.8) -> None:
    """Terminate this Streamlit server after the confirmation rerun renders.

    The launcher owns the Streamlit subprocess, so SIGTERM returns control to
    the launcher instead of requiring a keyboard interrupt.
    """
    pid = os.getpid()

    def _stop() -> None:
        time.sleep(delay)
        os.kill(pid, signal.SIGTERM)

    threading.Thread(target=_stop, name="streamlit-shutdown", daemon=True).start()


# ===========================================================================
# Project helpers
# ===========================================================================

PROVIDERS = list(PROVIDER_SPECS)


def _provider_label(provider_id: str) -> str:
    return PROVIDER_SPECS[provider_id]["label"]


def _project_exists(
    username: str,
    project_name: str,
) -> bool:
    """Return whether a project name already exists."""
    name = project_name.strip()

    if not name:
        return False

    try:
        existing_projects = list_projects(username)
    except Exception:
        return False

    return name in existing_projects


@st.dialog("Create new project")
def _new_project_dialog():
    """Display and process the complete new-project configuration dialog."""
    st.caption(
        "Create a project and configure its LLM provider and model."
    )

    project_name = st.text_input(
        "Project name",
        placeholder="e.g. invoice_pipeline",
        key="create_project_name",
    )

    provider = st.selectbox(
        "LLM provider",
        PROVIDERS,
        index=0,
        format_func=_provider_label,
        key="create_project_provider",
    )

    spec = PROVIDER_SPECS[provider]
    default_model = spec["default_model"]

    previous_provider = st.session_state.get(
        "create_project_previous_provider",
    )

    if previous_provider != provider:
        st.session_state[
            "create_project_model"
        ] = default_model

        st.session_state[
            "create_project_previous_provider"
        ] = provider

    model = st.text_input(
        spec["model_label"],
        value=st.session_state.get(
            "create_project_model",
            default_model,
        ),
        help=(
            "The model this project will use. "
            "The value is stored in the project's configuration."
        ),
        key="create_project_model",
    )

    endpoint = ""
    if spec["endpoint_env"]:
        endpoint = st.text_input(
            "Azure Foundry endpoint",
            value="",
            placeholder="https://<resource>.services.ai.azure.com",
            help=(
                "Shared by both Azure providers. Optional if the "
                f"{spec['endpoint_env']} environment variable is set."
            ),
            key="create_project_endpoint",
        )

    api_key = st.text_input(
        spec["key_label"],
        value="",
        type="password",
        help=(
            "Optional. Leave blank to use an API key supplied "
            "through the environment or existing configuration."
        ),
        key="create_project_api_key",
    )

    fallback_options = [
        item
        for item in PROVIDERS
        if item != provider
    ]

    fallback = st.multiselect(
        "Fallback providers",
        fallback_options,
        default=[],
        format_func=_provider_label,
        help=(
            "Providers that may be used as fallbacks if the primary "
            "provider cannot complete a request."
        ),
        key="create_project_fallbacks",
    )

    st.divider()

    left, right = st.columns(2)

    with left:
        create_clicked = st.button(
            "Create project",
            type="primary",
            use_container_width=True,
            key="create_project_submit",
        )

    with right:
        cancel_clicked = st.button(
            "Cancel",
            use_container_width=True,
            key="create_project_cancel",
        )

    if cancel_clicked:
        for key in (
            "create_project_name",
            "create_project_provider",
            "create_project_model",
            "create_project_api_key",
            "create_project_endpoint",
            "create_project_fallbacks",
            "create_project_previous_provider",
        ):
            st.session_state.pop(
                key,
                None,
            )

        st.rerun()

    if not create_clicked:
        return

    name = project_name.strip()

    if not name:
        st.error(
            "Enter a project name."
        )
        return

    if not model.strip():
        st.error(
            "Enter a model for the selected provider."
        )
        return

    if _project_exists(
        username,
        name,
    ):
        st.error(
            f"A project named **{name}** already exists. "
            "Choose a different project name."
        )
        return

    try:
        new_store = create_project(
            username,
            name,
        )

    except FileExistsError:
        st.error(
            f"A project named **{name}** already exists. "
            "Choose a different project name."
        )
        return

    except Exception as exc:
        st.error(
            f"Could not create project: {exc}"
        )
        return

    try:
        cfg = load_project_config(
            new_store,
        )

        provider_cfg = cfg.setdefault(
            "provider",
            {},
        )

        provider_cfg["name"] = provider
        provider_cfg["model"] = model.strip()
        provider_cfg["fallback"] = list(
            fallback
        )

        if endpoint.strip():
            provider_cfg["endpoint"] = endpoint.strip()

        if api_key.strip():
            provider_cfg.setdefault(
                "api_keys",
                {},
            )[spec["key_slot"]] = api_key.strip()

        cfg["provider"] = provider_cfg

        save_project_config(
            new_store,
            cfg,
        )

    except Exception as exc:
        st.error(
            f"Project `{name}` was created, but its provider "
            f"configuration could not be saved: {exc}"
        )
        return

    st.session_state.project_id = (
        new_store.project_id
    )

    st.session_state.pop(
        "chat_session_id",
        None,
    )

    st.session_state.messages = []
    _sync_browser_location(new_store.project_id, None)

    for key in (
        "create_project_name",
        "create_project_provider",
        "create_project_model",
        "create_project_api_key",
        "create_project_endpoint",
        "create_project_fallbacks",
        "create_project_previous_provider",
    ):
        st.session_state.pop(
            key,
            None,
        )

    st.rerun()


# ===========================================================================
# Project + persistent chat session bootstrap
# ===========================================================================

projects = list_projects(
    username,
)

if not projects:
    create_project(
        username,
        "default",
    )

    projects = [
        "default",
    ]

requested_project = _query_value("project")
if requested_project in projects:
    st.session_state.project_id = requested_project
elif st.session_state.project_id not in projects:
    st.session_state.project_id = projects[0]

store = get_project(
    username,
    st.session_state.project_id,
)

set_current_project(
    store,
)

sessions = ChatSessionStore(
    store,
)

session_list = sessions.list_sessions()

if not session_list:
    session = sessions.create()

else:
    current_id = (
        _query_value("chat")
        or st.session_state.get("chat_session_id")
    )

    session = sessions.load(current_id) if current_id else None

    if session is None:
        session = session_list[0]

st.session_state.chat_session_id = session["id"]

st.session_state.messages = session.get(
    "messages",
    [],
)

_sync_browser_location(st.session_state.project_id, session["id"])

# ===========================================================================
# Sidebar
# ===========================================================================

with st.sidebar:
    st.title(
        "Cognitive Persistence"
    )

    st.caption(
        username
    )

    st.markdown(
        "### Navigate"
    )

    _pending_drafts = len(
        DraftManager(store).list(
            status="pending",
        )
    )

    _nav_options = [
        "💬 Chat",
        "📁 Files",
        "🧠 Cognition",
        (
            f"📝 Review"
            f"{f' ({_pending_drafts})' if _pending_drafts else ''}"
        ),
        "📋 Instructions",
        "🕐 History",
        "⚙️ Settings",
    ]

    _nav_keys = [
        "chat",
        "files",
        "cognition",
        "review",
        "instructions",
        "history",
        "settings",
    ]

    st.session_state.setdefault(
        "nav_section",
        "chat",
    )

    if (
        st.session_state["nav_section"]
        in _nav_keys
    ):
        _current_nav_idx = _nav_keys.index(
            st.session_state["nav_section"]
        )
    else:
        _current_nav_idx = 0

    _nav_choice = st.radio(
        "Navigate",
        options=range(
            len(_nav_options)
        ),
        format_func=lambda i: _nav_options[i],
        index=_current_nav_idx,
        label_visibility="collapsed",
        key="nav_radio",
    )

    st.session_state["nav_section"] = (
        _nav_keys[_nav_choice]
    )

    st.divider()

    st.markdown("### Active agent")
    _agent_specs = AgentStore(store).list(enabled_only=True)
    _agent_ids = [""] + [a["id"] for a in _agent_specs]
    _agent_names = {"": "Default agent", **{a["id"]: a["name"] for a in _agent_specs}}
    st.session_state.setdefault("active_agent_id", "")
    if st.session_state["active_agent_id"] not in _agent_ids:
        st.session_state["active_agent_id"] = ""
    st.session_state["active_agent_id"] = st.selectbox(
        "Active agent", _agent_ids,
        index=_agent_ids.index(st.session_state["active_agent_id"]),
        format_func=lambda x: _agent_names.get(x, x),
        label_visibility="collapsed", key="active_agent_selector",
    )

    st.markdown("### Project")

    selected_project = st.selectbox(
        "Project", projects,
        index=projects.index(st.session_state.project_id),
        label_visibility="collapsed", key="project_selector",
    )

    with st.popover("Project actions", use_container_width=True):
        if st.button("＋ New project", use_container_width=True, key="sidebar_new_project"):
            _new_project_dialog()
        st.caption("Rename current project")
        project_rename = st.text_input(
            "New project name", value=st.session_state.project_id,
            label_visibility="collapsed", key="project_rename_input",
        )
        if st.button("Rename project", use_container_width=True, key="rename_project_btn"):
            try:
                renamed = rename_project(username, st.session_state.project_id, project_rename)
                st.session_state.project_id = renamed.project_id
                st.session_state.pop("chat_session_id", None)
                st.session_state.messages = []
                _sync_browser_location(renamed.project_id, None)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        if st.button("Delete project…", use_container_width=True, key="delete_selected_project"):
            st.session_state["confirm_delete_project"] = True

    if selected_project != st.session_state.project_id:
        st.session_state.project_id = selected_project
        st.session_state.pop("chat_session_id", None)
        st.session_state.messages = []
        _sync_browser_location(selected_project, None)
        st.rerun()

    if st.session_state.get("confirm_delete_project"):
        st.warning(f"Delete project '{st.session_state.project_id}' permanently?")
        dp1, dp2 = st.columns(2)
        with dp1:
            if st.button("Delete", key="confirm_project_delete", type="primary", use_container_width=True):
                project_to_delete = st.session_state.project_id
                delete_project(username, project_to_delete)
                remaining = list_projects(username)
                if not remaining:
                    create_project(username, "default")
                    remaining = ["default"]
                st.session_state.project_id = remaining[0]
                st.session_state.pop("chat_session_id", None)
                st.session_state.messages = []
                st.session_state.pop("confirm_delete_project", None)
                _sync_browser_location(st.session_state.project_id, None)
                st.rerun()
        with dp2:
            if st.button("Cancel", key="cancel_project_delete", use_container_width=True):
                st.session_state.pop("confirm_delete_project", None)
                st.rerun()

    st.divider()

    st.markdown(
        "### Chat sessions"
    )

    session_list = sessions.list_sessions()

    session_ids = [
        s["id"]
        for s in session_list
    ]

    current_idx = (
        session_ids.index(
            st.session_state.chat_session_id
        )
        if (
            st.session_state.chat_session_id
            in session_ids
        )
        else 0
    )

    session_choice = st.selectbox(
        "Session",
        session_list,
        index=current_idx,
        format_func=lambda s: (
            s.get("title")
            or "New chat"
        ),
        label_visibility="collapsed",
        key=f"session_selector_{store.project_id}",
    )

    if (
        session_choice["id"]
        != st.session_state.chat_session_id
    ):
        loaded = sessions.load(
            session_choice["id"]
        )

        if loaded:
            st.session_state.chat_session_id = (
                loaded["id"]
            )

            st.session_state.messages = (
                loaded.get(
                    "messages",
                    [],
                )
            )
            _sync_browser_location(st.session_state.project_id, loaded["id"])

            st.rerun()

    sc1, sc2 = st.columns(2)

    with sc1:
        if st.button(
            "＋ New chat",
            use_container_width=True,
        ):
            new_session = sessions.create()

            st.session_state.chat_session_id = (
                new_session["id"]
            )

            st.session_state.messages = []
            _sync_browser_location(st.session_state.project_id, new_session["id"])

            st.rerun()

    with sc2:
        if st.button(
            "Rename",
            use_container_width=True,
        ):
            st.session_state.rename_session = True

    if st.button(
        "Delete current chat",
        use_container_width=True,
    ):
        st.session_state[
            "confirm_delete_chat"
        ] = True

    if st.session_state.get(
        "confirm_delete_chat"
    ):
        st.warning(
            "Delete this chat permanently?"
        )

        dc1, dc2 = st.columns(2)

        with dc1:
            if st.button(
                "Delete chat",
                key="confirm_chat_delete",
                type="primary",
                use_container_width=True,
            ):
                deleted_id = (
                    st.session_state.chat_session_id
                )

                sessions.delete(
                    deleted_id
                )

                remaining = (
                    sessions.list_sessions()
                )

                if remaining:
                    session = remaining[0]

                else:
                    session = sessions.create()

                st.session_state.chat_session_id = (
                    session["id"]
                )

                st.session_state.messages = (
                    session.get(
                        "messages",
                        [],
                    )
                )

                st.session_state.pop(
                    "confirm_delete_chat",
                    None,
                )
                _sync_browser_location(st.session_state.project_id, session["id"])

                st.rerun()

        with dc2:
            if st.button(
                "Cancel",
                key="cancel_chat_delete",
                use_container_width=True,
            ):
                st.session_state.pop(
                    "confirm_delete_chat",
                    None,
                )
                _sync_browser_location(st.session_state.project_id, session["id"])

                st.rerun()

    if st.session_state.get(
        "rename_session"
    ):
        rename = st.text_input(
            "Session name",
            value=session.get(
                "title",
                "",
            ),
            key="session_rename_input",
        )

        if st.button(
            "Save name",
            type="primary",
            use_container_width=True,
        ):
            sessions.rename(
                st.session_state.chat_session_id,
                rename,
            )

            st.session_state.pop(
                "rename_session",
                None,
            )

            st.rerun()

    st.divider()

    st.caption("Project status")

    source_file_count = sum(
        p.is_file()
        for p in store.source.rglob("*")
    )

    workspace_file_count = sum(
        p.is_file()
        for p in store.workspace.rglob("*")
    )

    st.write(
        f"Files: "
        f"{source_file_count + workspace_file_count}"
    )

    st.write(
        "Cognition entities: "
        f"{len(store.state_map().get('entities', {}))}"
    )

    st.write(
        "Cognition version: "
        f"{store.state_map().get('current_version', 0)}"
    )

    with st.popover("System", use_container_width=True):
        if st.button("Log out", use_container_width=True, key="system_logout"):
            logout()

        if st.button("Shut down UI…", use_container_width=True, key="system_shutdown_request"):
            st.session_state["confirm_shutdown_ui"] = True

        if st.session_state.get("confirm_shutdown_ui"):
            st.warning("This stops the Streamlit server for every connected browser.")
            sc1, sc2 = st.columns(2)
            with sc1:
                if st.button("Shut down", type="primary", use_container_width=True, key="system_shutdown_confirm"):
                    st.session_state["shutdown_scheduled"] = True
                    st.session_state.pop("confirm_shutdown_ui", None)
                    _terminate_streamlit_process_after_response()
                    st.rerun()
            with sc2:
                if st.button("Cancel", use_container_width=True, key="system_shutdown_cancel"):
                    st.session_state.pop("confirm_shutdown_ui", None)
                    st.rerun()

    if st.session_state.get("shutdown_scheduled"):
        st.info("Shutting down the UI. The launcher will regain control shortly.")

# ===========================================================================
# Main navigation
# ===========================================================================

st.title(
    "Cognitive Persistence Agent"
)

st.caption(
    "Persistent conversations · "
    "project-scoped cognition · "
    "general-purpose file management"
)

nav_section = st.session_state[
    "nav_section"
]

# ===========================================================================
# Chat artifact preview
# ===========================================================================


def _new_tab_link(label: str, mime: str, data: bytes) -> str:
    """Small icon-only button that opens the selected file in a new browser tab.

    This used to be a plain `<a href="data:...">` link with target="_blank",
    but browsers (Chrome in particular) block top-level navigation to large
    `data:` URIs as a security measure: the new tab opens blank and the file
    only shows up after a manual reload. Building a `Blob` from the bytes and
    opening an `URL.createObjectURL()` link instead sidesteps that
    restriction entirely, so the tab shows the file immediately.
    """
    encoded = base64.b64encode(data).decode("ascii")
    return f"""
<!DOCTYPE html>
<html>
<head>
<style>
  html, body {{
      margin: 0;
      padding: 0;
      height: 100%;
      overflow: hidden;
      background: transparent;
  }}
  body {{
      display: flex;
      justify-content: flex-end;
      align-items: center;
  }}
  #open-in-new-tab {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 26px;
      height: 26px;
      border: 1px solid rgba(128,128,128,.35);
      border-radius: 5px;
      background: transparent;
      cursor: pointer;
      font-size: 14px;
      line-height: 1;
      padding: 0;
      font-family: "Source Sans Pro", sans-serif;
      color: #666;
  }}
  @media (prefers-color-scheme: dark) {{
      #open-in-new-tab {{
          color: #bbb;
          border-color: rgba(255,255,255,.35);
      }}
  }}
</style>
</head>
<body>
  <button id="open-in-new-tab" title="{label}" aria-label="{label}">↗</button>
  <script>
    document.getElementById('open-in-new-tab').addEventListener('click', function () {{
        const binary = atob('{encoded}');
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {{
            bytes[i] = binary.charCodeAt(i);
        }}
        const blob = new Blob([bytes], {{ type: '{mime}' }});
        const url = URL.createObjectURL(blob);
        window.open(url, '_blank');
        setTimeout(function () {{ URL.revokeObjectURL(url); }}, 60000);
    }});
  </script>
</body>
</html>
"""


def _collect_preview_files(store):
    """Return user-visible Workspace/Source files for the preview selector."""
    files = []
    for area, root in (("Workspace", store.workspace), ("Source", store.source)):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            # Internal application state must never appear in the user file selector.
            relative = path.relative_to(root)
            if any(part == ".system" for part in relative.parts):
                continue
            if any(part == "drafts" for part in relative.parts):
                continue
            files.append((area, path, root))
    files.sort(key=lambda item: item[1].stat().st_mtime, reverse=True)
    return files


def _render_mermaid_preview(diagram_source: str) -> None:
    """Render Mermaid syntax as an actual diagram, not just its source text.

    The diagram text is passed into the iframe as a JSON string literal
    (rather than interpolated into the HTML) so that characters like <, >,
    &, and quotes inside the Mermaid source can't break the surrounding
    markup; mermaid.js then parses it and renders real SVG client-side.
    """
    payload = json.dumps(diagram_source)
    document = f"""
<div id="mermaid-target" style="width:100%;overflow:auto;"></div>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<script>
  mermaid.initialize({{ startOnLoad: false, securityLevel: "loose" }});
  const target = document.getElementById("mermaid-target");
  const source = {payload};
  mermaid.render("mermaid-diagram", source)
    .then(({{ svg }}) => {{ target.innerHTML = svg; }})
    .catch((err) => {{
        target.innerHTML =
            "<pre style='color:#c0392b;white-space:pre-wrap;'>" +
            "Mermaid render error: " + (err && err.message ? err.message : err) +
            "</pre>";
    }});
</script>
"""
    st.components.v1.html(document, height=650, scrolling=True)


def _render_selected_file(path: Path, root: Path) -> None:
    """Render only the selected file; the dialog itself owns scrolling."""
    suffix = path.suffix.lower()

    try:
        raw = path.read_bytes()
    except Exception as exc:
        st.error(f"Could not read `{path.name}`: {exc}")
        return

    mime_by_suffix = {
        ".html": "text/html",
        ".htm": "text/html",
        ".css": "text/css",
        ".js": "text/javascript",
        ".json": "application/json",
        ".xml": "application/xml",
        ".svg": "image/svg+xml",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".mmd": "text/plain",
        ".py": "text/plain",
        ".yaml": "text/plain",
        ".yml": "text/plain",
    }
    mime = mime_by_suffix.get(suffix, "application/octet-stream")

    new_tab_mime, new_tab_bytes = mime, raw
    if suffix == ".svg":
        # A standalone .svg document is parsed as strict XML by the browser,
        # so a bare "&" or other minor entity glitch inside it (which HTML's
        # lenient parser silently tolerates -- which is why it renders fine
        # in chat and in the in-app preview below) turns into a hard XML
        # parse error page. Wrapping it in a tiny HTML document instead makes
        # the new-tab view use the same lenient parsing as everywhere else,
        # so it can't diverge from what already works.
        svg_text = raw.decode("utf-8", errors="replace")
        new_tab_mime = "text/html"
        new_tab_bytes = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<style>html,body{margin:0;height:100%;}"
            "body{display:flex;align-items:center;justify-content:center;}"
            "svg{max-width:100%;max-height:100%;}</style></head><body>"
            f"{svg_text}</body></html>"
        ).encode("utf-8")

    # Tiny open-in-new-tab control. No width control or split-pane control exists.
    # Rendered via components.html (an iframe), not st.markdown, because the
    # button's onclick handler needs actual JS execution -- st.markdown's
    # unsafe_allow_html inserts inert HTML and won't run inline scripts/handlers.
    st.components.v1.html(
        _new_tab_link(
            "Open file in new browser tab",
            new_tab_mime,
            new_tab_bytes,
        ),
        height=40,
    )

    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        st.image(raw, use_container_width=True)
        return

    if suffix in DOCUMENT_SUFFIXES:
        render_document_preview(path, key="chat")
        return

    try:
        content = raw.decode("utf-8", errors="replace")
    except Exception:
        st.info("This file cannot be displayed as text in the preview.")
        return

    if suffix in {".html", ".htm"}:
        try:
            document = _prepare_html_preview(path, root)
            st.components.v1.html(document, height=600, scrolling=True)
        except Exception as exc:
            st.error(f"Could not preview `{path.name}`: {exc}")
        return

    if suffix == ".md":
        st.markdown(content)
        return

    if suffix == ".svg":
        # Render the SVG itself (it's valid HTML markup) inside an isolated
        # iframe, the same way .html files are previewed below, instead of
        # dumping the markup as a code block.
        try:
            st.components.v1.html(
                f'<div style="width:100%;height:100%;display:flex;'
                f'align-items:center;justify-content:center;overflow:auto;">'
                f'{content}</div>',
                height=650,
                scrolling=True,
            )
        except Exception as exc:
            st.error(f"Could not render `{path.name}`: {exc}")
            st.code(content, language="xml")
        return

    if suffix == ".mmd":
        # Keep Mermaid source visible even when the browser-side renderer is unavailable.
        # A Mermaid file can contain either raw Mermaid syntax or a markdown fence.
        stripped = content.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            first_newline = stripped.find("\n")
            if first_newline >= 0:
                stripped = stripped[first_newline + 1:-3].strip()
        try:
            _render_mermaid_preview(stripped)
        except Exception as exc:
            st.error(f"Could not render Mermaid diagram: {exc}")
            st.code(stripped, language="text")
        return

    language = {
        ".py": "python",
        ".js": "javascript",
        ".css": "css",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".xml": "xml",
        ".svg": "xml",
        ".sql": "sql",
        ".java": "java",
        ".cs": "csharp",
        ".cpp": "cpp",
        ".c": "c",
        ".ts": "typescript",
    }.get(suffix, "text")
    st.code(content, language=language)


def _disable_file_preview_toggle():
    """Turn the "Show file preview" toggle back off once the dialog closes,
    whether it's closed via the X button, Esc, clicking outside, or
    programmatically -- so it never stays "on" while nothing is showing."""
    st.session_state["chat_preview_enabled"] = False


@st.dialog(
    "File preview",
    width="large",
    on_dismiss=_disable_file_preview_toggle,
)
def _open_file_preview_dialog(store):
    """Display the file selector and preview inside a viewport-bounded dialog."""
    files = _collect_preview_files(store)

    if not files:
        st.info("No project files yet.")
        return

    labels = [
        f"[{area}] {path.relative_to(root)}"
        for area, path, root in files
    ]

    selected = st.selectbox(
        "File",
        labels,
        key="chat_preview_select",
    )
    idx = labels.index(selected)
    area, path, root = files[idx]

    _render_selected_file(path, root)


# Keep the dialog below the browser viewport. The dialog itself does not grow
# beyond 90% of the screen; its contents scroll inside the dialog.
st.markdown(
    """
<style>
/* File preview dialog: never exceed 90% of viewport height. */
div[data-testid="stDialog"] div[role="dialog"] {
    max-height: 90vh !important;
    height: 90vh !important;
}

div[data-testid="stDialog"] div[role="dialog"] > div {
    max-height: 90vh !important;
    overflow-y: auto !important;
}
</style>
""",
    unsafe_allow_html=True,
)


# Persistent tool-call helpers
# ===========================================================================

def _serialise_tool_calls(calls):
    """Keep the legacy tool-call list for existing session/API compatibility."""
    result = []
    for call in calls or []:
        if not isinstance(call, dict):
            continue
        result.append({
            "tool": call.get("tool"),
            "args": call.get("args"),
            "result": call.get("result"),
        })
    return result


def _render_execution_trace(trace_data):
    """Render the complete persisted agent execution trace.

    The trace is deliberately independent of tool calls. API calls, retrieval,
    context assembly, tools, errors and finalization are all first-class events.
    """
    if not trace_data:
        return
    events = trace_data.get("events") if isinstance(trace_data, dict) else trace_data
    if not events:
        return

    with st.expander(f"Execution trace · {len(events)} events", expanded=False):
        for event in events:
            if not isinstance(event, dict):
                continue
            seq = event.get("seq", "?")
            event_type = str(event.get("type") or "event").replace("_", " ").title()
            elapsed = event.get("elapsed_ms")
            suffix = f" · {elapsed} ms" if elapsed is not None else ""
            data = event.get("data") or {}
            with st.expander(f"{seq}. {event_type}{suffix}", expanded=False):
                if data:
                    st.json(data)


def _render_tool_calls(tool_calls):
    """Render the old tool-only trace when loading legacy chat sessions."""
    if not tool_calls:
        return
    with st.expander(f"Legacy tool trace · {len(tool_calls)} tool calls", expanded=False):
        for index, call in enumerate(tool_calls, start=1):
            st.markdown(f"**Tool call {index}: `{call.get('tool', 'unknown')}`**")
            if call.get("args") is not None:
                st.markdown("**Arguments**")
                st.json(call.get("args"))
            if call.get("result") is not None:
                st.markdown("**Result**")
                result = call.get("result")
                if isinstance(result, (dict, list)):
                    st.json(result)
                else:
                    st.code(str(result))


# ===========================================================================
# Chat
# ===========================================================================

if nav_section == "chat":
    from ragapp.core.vcs import VCSManager

    preview_enabled = st.toggle("Show file preview", value=False, key="chat_preview_enabled", help="Show or hide the optional file preview panel.")
    trace_enabled = st.toggle(
        "Execution trace",
        value=st.session_state.get("execution_trace_enabled", True),
        key="execution_trace_enabled",
        help="Development instrumentation. Shows retrieval, model/API, tool, and final-turn events. Disable for normal use.",
    )
    chat_col = st.container()

    def _activate_turn(target_turn_id):
        """Switch chat lineage and materialize that lineage's project state."""
        current = sessions.load(st.session_state.chat_session_id)
        turn = (current.get("turns") or {}).get(target_turn_id) if current else None
        if not turn:
            return
        target_state = turn.get("state_after") or turn.get("state_before")
        if target_state:
            VCSManager(store).materialize_state(target_state, message=f"Activate chat branch {target_turn_id[:8]}")
        refreshed=sessions.set_active_leaf(st.session_state.chat_session_id, target_turn_id)
        active_drafts=[]
        for active_tid in sessions.lineage_ids_data(refreshed):
            active_drafts.extend(((refreshed.get("turns") or {}).get(active_tid,{}).get("effects") or {}).get("draft_ids") or [])
        DraftManager(store).set_session_active_drafts(st.session_state.chat_session_id, active_drafts)
        st.session_state.messages=refreshed.get("messages",[])

    def _run_chat_turn(user_text, *, parent_id=None, replacing_turn_id=None):
        current=sessions.load(st.session_state.chat_session_id) or session
        turns=current.get("turns") or {}
        if replacing_turn_id:
            old=turns.get(replacing_turn_id) or {}
            parent_id=old.get("parent_id")
            # Editing means fork from the exact state before the old turn.
            base_state=old.get("state_before")
            if base_state:
                VCSManager(store).materialize_state(base_state, message=f"Fork chat before {replacing_turn_id[:8]}")
        elif parent_id is None:
            parent_id=current.get("active_leaf_id")

        vcs=VCSManager(store)
        state_before=vcs.checkpoint(f"Before chat turn: {user_text[:48]}")
        turn_id=uuid.uuid4().hex
        base_transcript=sessions.active_transcript_data(current,parent_id) if parent_id else []
        user_message={"role":"user","content":user_text,"turn_id":turn_id}
        transcript=base_transcript+[user_message]
        active_ids=sessions.lineage_ids_data(current,parent_id) if parent_id else []
        active_ids=active_ids+[turn_id]
        calls=[]; drafts=[]; answer=""; tool_calls=[]
        execution_trace = ExecutionTrace(enabled=trace_enabled) if trace_enabled else None
        placeholder=st.empty()
        stream_state={"rendered":"","plan":None,"done":[]}
        def _render_plan():
            return "**Planned sections:**\n"+"\n".join(f"- {'✅' if i < len(stream_state['done']) else '⏳'} {title}" for i,title in enumerate(stream_state["plan"] or []))
        def _on_section(event):
            if event["type"]=="plan":
                stream_state["plan"]=event["sections"]; stream_state["done"]=[]; placeholder.markdown(_render_plan())
            elif event["type"]=="section":
                if stream_state["plan"] is not None: stream_state["done"].append(event["title"])
                stream_state["rendered"]+=("\n\n" if stream_state["rendered"] else "")+event["content"]; placeholder.markdown(stream_state["rendered"])
        try:
            with st.spinner("Executing…"):
                set_current_project(store)
                tools=build_default_tools(username,store,include_cognition=True,session_id=st.session_state.chat_session_id)
                answer,calls,drafts=run_agent(AgentRunContext(
                    transcript=transcript, tools=tools, cognition=store, project_id=store.project_id,
                    session_id=st.session_state.chat_session_id, turn_id=turn_id, active_turn_ids=active_ids,
                    on_section=_on_section, agent_id=(st.session_state.get("active_agent_id") or None),
                    trace=execution_trace,
                ))
            answer=(answer or "").strip() or stream_state["rendered"] or "⚠️ The agent produced no response for this turn. Try again."
            tool_calls=_serialise_tool_calls(calls)
            placeholder.markdown(answer)
            if execution_trace is not None:
                _render_execution_trace(execution_trace.as_dict())
            else:
                _render_tool_calls(tool_calls)
        except Exception as exc:
            answer=(stream_state["rendered"]+"\n\n" if stream_state["rendered"] else "")+"The agent encountered an error while executing this request."
            tool_calls=_serialise_tool_calls(calls)+[{"tool":"agent_error","args":{},"result":str(exc)}]
            placeholder.markdown(answer)
            if execution_trace is not None:
                _render_execution_trace(execution_trace.as_dict())
            else:
                _render_tool_calls(tool_calls)
        state_after=vcs.checkpoint(f"After chat turn: {user_text[:48]}")
        assistant_message={"role":"assistant","content":answer,"tool_calls":tool_calls,"turn_id":turn_id}
        if execution_trace is not None:
            assistant_message["execution_trace"] = execution_trace.as_dict()
        effects={"draft_ids":[d.get("id") for d in drafts if isinstance(d,dict)],"tool_count":len(calls)}
        if replacing_turn_id:
            refreshed=sessions.fork_turn(
                st.session_state.chat_session_id,
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
            refreshed=sessions.append_turn(
                st.session_state.chat_session_id,
                user_message,
                assistant_message,
                title_from=user_text,
                parent_id=parent_id,
                state_before=state_before,
                state_after=state_after,
                effects=effects,
                turn_id=turn_id,
            )
        active_drafts=[]
        for active_tid in sessions.lineage_ids_data(refreshed):
            active_drafts.extend(((refreshed.get("turns") or {}).get(active_tid,{}).get("effects") or {}).get("draft_ids") or [])
        DraftManager(store).set_session_active_drafts(st.session_state.chat_session_id, active_drafts)
        st.session_state.messages=refreshed.get("messages",[])

    with chat_col:
        chat_box=st.container(height=300,border=False)
        current_session=sessions.load(st.session_state.chat_session_id) or session
        active_ids=current_session and sessions.lineage_ids_data(current_session) or []
        with chat_box:
            if not active_ids:
                st.markdown("Start a task. You can ask the agent to create, inspect, edit, move, copy, or delete project files without uploading anything first.")
            for tid in active_ids:
                turn=current_session["turns"][tid]
                with st.chat_message("user"):
                    st.markdown((turn.get("user") or {}).get("content",""))
                    parent=turn.get("parent_id")
                    siblings=[x for x in (current_session.get("turns") or {}).values() if x.get("parent_id")==parent]
                    cols=st.columns([1,1,6])
                    if cols[0].button("Edit",key=f"edit_{tid}"):
                        st.session_state[f"editing_{tid}"]=True
                    if len(siblings)>1:
                        idx=next((i for i,x in enumerate(siblings) if x.get("id")==tid),0)
                        cols[1].caption(f"{idx+1}/{len(siblings)}")
                    if st.session_state.get(f"editing_{tid}"):
                        edited=st.text_area("Edit prompt",value=(turn.get("user") or {}).get("content",""),key=f"edit_text_{tid}")
                        ec1,ec2=st.columns(2)
                        if ec1.button("Submit edit",key=f"submit_edit_{tid}"):
                            st.session_state["pending_chat_edit"]={"turn_id":tid,"text":edited}; st.session_state[f"editing_{tid}"]=False; st.rerun()
                        if ec2.button("Cancel",key=f"cancel_edit_{tid}"):
                            st.session_state[f"editing_{tid}"]=False; st.rerun()
                with st.chat_message("assistant"):
                    a=turn.get("assistant") or {}; st.markdown(a.get("content",""))
                    if a.get("execution_trace"):
                        _render_execution_trace(a.get("execution_trace"))
                    else:
                        _render_tool_calls(a.get("tool_calls",[]))
                    # Switch among sibling branches without deleting either branch.
                    siblings=[x for x in (current_session.get("turns") or {}).values() if x.get("parent_id")==turn.get("parent_id")]
                    if len(siblings)>1:
                        labels=[((x.get("user") or {}).get("content","")[:35] or x["id"][:8]) for x in siblings]
                        chosen=st.selectbox("Branch",range(len(siblings)),format_func=lambda i:labels[i],index=next((i for i,x in enumerate(siblings) if x["id"]==tid),0),key=f"branch_{tid}",label_visibility="collapsed")
                        if siblings[chosen]["id"]!=tid:
                            _activate_turn(siblings[chosen]["id"]); st.rerun()

        pending=st.session_state.pop("pending_chat_edit",None)
        if pending:
            with chat_box:
                with st.chat_message("assistant"):
                    _run_chat_turn(pending["text"],replacing_turn_id=pending["turn_id"])
            st.rerun()

        prompt=st.chat_input("Ask a question or give the agent a task…")
        if prompt:
            with chat_box:
                with st.chat_message("user"): st.markdown(prompt)
                with st.chat_message("assistant"): _run_chat_turn(prompt)
            st.rerun()

    if preview_enabled:
        _open_file_preview_dialog(store)

# ===========================================================================
# Files
# ===========================================================================

if nav_section == "files":
    render_file_manager(
        store
    )

# ===========================================================================
# Cognition
# ===========================================================================

if nav_section == "cognition":
    st.subheader("Persistent cognition")

    state = store.state_map()
    counts = store.cognition_counts()

    metric_slots = {}
    row1 = st.columns(4)
    row2 = st.columns(4)
    metric_spec = [
        ("entities", "Entities"),
        ("relationships", "Relationships"),
        ("events", "Events"),
        ("locations", "Locations"),
        ("concepts", "Concepts"),
        ("definitions", "Definitions"),
        ("knowledge", "Knowledge"),
        ("version", "Version"),
    ]
    for col, (key, label) in zip(row1 + row2, metric_spec):
        metric_slots[key] = col.empty()
        value = state.get("current_version", 0) if key == "version" else counts.get(key, 0)
        metric_slots[key].metric(label, value)

    if st.session_state.get("compile_success_msg"):
        st.success(st.session_state.pop("compile_success_msg"))
    elif state.get("compiled"):
        st.success("Cognition has been compiled from project artifacts.")
    else:
        st.info(
            "Cognition is ready but has not been compiled from project artifacts. "
            "This does not block chat or file work."
        )

    available = list_available_files(store)

    if not available:
        st.caption("No files in Source or Workspace yet — add some under the Files tab first.")
    else:
        labels = [f"[{f['area']}] {f['path']}  ·  {f['size']}" for f in available]
        label_to_key = {lbl: (f["area"], f["path"]) for lbl, f in zip(labels, available)}
        chosen_labels = st.multiselect(
            "Files to compile", labels, default=labels, key="compile_file_picker"
        )
        chosen = [label_to_key[l] for l in chosen_labels]
        st.caption(
            f"{len(chosen)} of {len(available)} file(s) selected. Unselected files keep "
            "their existing cognition; selected files are extracted and merged in this run."
        )

        if st.button("Compile selected files", type="primary", disabled=not chosen):
            try:
                baseline = store.cognition_counts()
                progress = st.progress(0, text="Preparing selected files…")
                status = st.empty()
                live = st.empty()

                def render_live_counts(done, total):
                    current = store.cognition_counts()
                    for key, label in metric_spec:
                        if key == "version":
                            metric_slots[key].metric(label, store.state_map().get("current_version", 0))
                            continue
                        value = current.get(key, 0)
                        delta = value - baseline.get(key, 0)
                        metric_slots[key].metric(label, value, delta if delta else None)
                    with live.container(border=True):
                        st.caption("Live durable cognition")
                        st.write(
                            " · ".join(
                                f"{label}: {current.get(key, 0)}"
                                for key, label in metric_spec
                                if key != "version"
                            )
                        )
                    progress.progress(
                        done / total if total else 0,
                        text=f"Compiling batch {done}/{total}" if total else "Compiling…",
                    )
                    status.info(
                        f"Batch {done}/{total} merged into cognition. Counts above reflect durable state."
                        if total else "Compiling cognition…"
                    )

                status.info("Compilation started — preparing source…")
                result = compile_project(
                    store,
                    selected_files=chosen,
                    progress_callback=render_live_counts,
                )
                final_counts = store.cognition_counts()
                render_live_counts(1, 1)
                progress.progress(1.0, text="Compilation complete")
                status.success("Compilation complete.")
                st.session_state["compile_success_msg"] = (
                    f"Processed {result.get('source_files', 0)} source + "
                    f"{result.get('workspace_files', 0)} workspace file(s). "
                    + ", ".join(f"{k}: {v}" for k, v in final_counts.items())
                )
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    with st.expander(
        "Ledger",
        expanded=False,
    ):
        st.json(
            store.ledger()
        )

    with st.expander(
        "Relationships",
        expanded=False,
    ):
        st.json(
            store.relationships()
        )

    with st.expander(
        "State map",
        expanded=False,
    ):
        st.json(
            state
        )

# ===========================================================================
# Draft review
# ===========================================================================

if nav_section == "review":
    st.subheader(
        "Draft review"
    )

    st.caption(
        "AI output is persisted in the workspace. "
        "Approval is the only path that writes an "
        "agent-generated change into /source."
    )

    dm = DraftManager(
        store
    )

    drafts = dm.list()

    if not drafts:
        st.info(
            "No drafts yet."
        )

    for d in drafts:
        with st.expander(
            (
                f"{d.get('status', 'pending').upper()} "
                f"· {d['id'][:8]} "
                f"· "
                f"{d.get('metadata', {}).get('target_file', 'conversation response')}"
            ),
            expanded=(
                d.get("status") == "pending"
            ),
        ):
            meta = d.get(
                "metadata",
                {},
            )

            content = st.text_area(
                "Draft content",
                d.get(
                    "content",
                    "",
                ),
                height=320,
                key=f"draft_content_{d['id']}",
            )

            target = st.text_input(
                "Target file",
                meta.get(
                    "target_file",
                    "",
                ),
                key=f"draft_target_{d['id']}",
            )

            desc = st.text_input(
                "Change description",
                meta.get(
                    "change_description",
                    "",
                ),
                key=f"draft_desc_{d['id']}",
            )

            c1, c2, c3 = st.columns(3)

            if d.get("status") == "pending":
                if c1.button(
                    "Save edits",
                    key=f"draft_save_{d['id']}",
                ):
                    d["content"] = content

                    d["metadata"] = {
                        **meta,
                        "target_file": target,
                        "change_description": desc,
                    }

                    dm.save(
                        d
                    )

                    st.success(
                        "Draft saved."
                    )

                    st.rerun()

                if c2.button(
                    "Approve",
                    type="primary",
                    key=f"draft_approve_{d['id']}",
                ):
                    try:
                        ApprovalEngine(
                            store
                        ).approve(
                            d["id"]
                        )

                        st.success(
                            "Approved and committed to source."
                        )

                        st.rerun()

                    except Exception as exc:
                        st.error(
                            str(exc)
                        )

                if c3.button(
                    "Reject",
                    key=f"draft_reject_{d['id']}",
                ):
                    st.session_state[
                        f"rejecting_{d['id']}"
                    ] = True

                if st.session_state.get(
                    f"rejecting_{d['id']}"
                ):
                    reason = st.text_area(
                        "Why was this rejected?",
                        key=f"draft_reason_{d['id']}",
                    )

                    if st.button(
                        "Confirm rejection",
                        key=f"draft_reject_confirm_{d['id']}",
                    ):
                        ApprovalEngine(
                            store
                        ).reject(
                            d["id"],
                            reason,
                        )

                        st.rerun()

# ===========================================================================
# Instructions
# ===========================================================================

if nav_section == "instructions":
    st.subheader(
        "Persistent instructions"
    )

    st.caption(
        "Standing directives and rejection-derived "
        "constraints survive chat-session clearing."
    )

    ins = InstructionStore(
        store
    )

    with st.expander(
        "Add instruction",
        expanded=False,
    ):
        content = st.text_area(
            "Instruction"
        )

        scope = st.selectbox(
            "Scope",
            [
                "system",
                "file_tagged",
                "situational",
            ],
        )

        tag = (
            st.text_input(
                "Tagged entity ID (for file_tagged)"
            )
            if scope == "file_tagged"
            else None
        )

        if (
            st.button(
                "Add instruction",
                type="primary",
            )
            and content.strip()
        ):
            ins.add(
                content.strip(),
                scope,
                tag,
                "user_stated",
            )

            st.rerun()

    for i in ins.list():
        with st.expander(
            (
                f"{i['status']} "
                f"· {i['scope']} "
                f"· {i['content'][:80]}"
            )
        ):
            edited = st.text_area(
                "Content",
                i["content"],
                key=f"instr_{i['id']}",
            )

            if i["status"] == "active":
                a, b = st.columns(2)

                if a.button(
                    "Save",
                    key=f"isave_{i['id']}",
                ):
                    ins.update(
                        i["id"],
                        edited,
                    )

                    st.rerun()

                if b.button(
                    "Deactivate",
                    key=f"ideact_{i['id']}",
                ):
                    ins.deactivate(
                        i["id"]
                    )

                    st.rerun()

# ===========================================================================
# History
# ===========================================================================

if nav_section == "history":
    st.subheader(
        "Git history"
    )

    vcs = VCSManager(
        store
    )

    log = (
        vcs.log()
        if (store.root / ".git").exists()
        else ""
    )

    st.code(
        log
        or "No commits yet."
    )

    st.caption(
        "Past states can be inspected through commit "
        "hashes; reversion is deliberately separate "
        "from ordinary draft approval."
    )

# ===========================================================================
# Settings
# ===========================================================================

if nav_section == "settings":
    st.subheader(
        "Project settings"
    )

    cfg = load_project_config(
        store
    )

    prov = cfg.setdefault(
        "provider",
        {},
    )

    limits = cfg.setdefault(
        "limits",
        {},
    )

    features = cfg.setdefault(
        "features",
        {},
    )

    old_provider = prov.get(
        "name",
        "gemini",
    )

    if old_provider not in PROVIDER_SPECS:
        st.warning(
            f"This project uses '{old_provider}', which is not a supported "
            "provider. Choose one below and save."
        )

    provider = st.selectbox(
        "LLM provider",
        PROVIDERS,
        index=(
            PROVIDERS.index(
                old_provider
            )
            if old_provider in PROVIDERS
            else 0
        ),
        format_func=_provider_label,
    )

    spec = PROVIDER_SPECS[provider]

    if provider != old_provider:
        prov["model"] = spec["default_model"]

    prov["name"] = provider

    prov["model"] = st.text_input(
        spec["model_label"],
        prov.get(
            "model",
            "",
        ),
        help=(
            "This model is stored with the project "
            "and must belong to the selected provider."
        ),
    )

    if spec["endpoint_env"]:
        prov["endpoint"] = st.text_input(
            "Azure Foundry endpoint",
            prov.get(
                "endpoint",
                "",
            ),
            placeholder="https://<resource>.services.ai.azure.com",
            help=(
                "Shared by both Azure providers. The "
                f"{spec['endpoint_env']} environment variable "
                "takes precedence."
            ),
        )

    key = st.text_input(
        spec["key_label"],
        value="",
        type="password",
        help=(
            "Leave blank to keep the existing "
            "configured key. Environment variables "
            "take precedence."
        ),
    )

    fallback = st.multiselect(
        "Fallback providers",
        [x for x in PROVIDERS if x != provider],
        default=[
            x
            for x in prov.get(
                "fallback",
                [],
            )
            if x in PROVIDERS and x != provider
        ],
        format_func=_provider_label,
    )

    prov["fallback"] = fallback

    limits["max_agent_steps"] = st.number_input(
        "Max agent steps",
        1,
        100,
        int(limit(cfg, "max_agent_steps")),
    )

    limits["transcript_turns"] = st.number_input(
        "Transcript clearing turn threshold",
        5,
        1000,
        int(limit(cfg, "transcript_turns")),
    )

    limits["transcript_chars"] = st.number_input(
        "Transcript clearing character threshold",
        1000,
        1000000,
        int(limit(cfg, "transcript_chars")),
    )

    limits["requests_per_minute"] = st.number_input(
        "Provider requests/minute",
        1,
        1000,
        int(limit(cfg, "requests_per_minute")),
    )

    features["semantic_propagation"] = st.checkbox(
        "Semantic propagation",
        bool(
            features.get(
                "semantic_propagation",
                True,
            )
        ),
    )

    if st.button(
        "Save settings",
        type="primary",
    ):
        if key:
            prov.setdefault(
                "api_keys",
                {}
            )[spec["key_slot"]] = key

        cfg["provider"] = prov
        cfg["limits"] = limits
        cfg["features"] = features

        save_project_config(
            store,
            cfg,
        )

        st.success(
            "Settings saved to this project."
        )