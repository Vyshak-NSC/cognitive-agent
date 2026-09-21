from pathlib import Path
import base64
import json

import streamlit as st
from ragapp.auth.db import initialize_database
from ragapp.interfaces.streamlit_app.auth_ui import login
from ragapp.cognition.compiler import compile_project, list_available_files
from ragapp.execution.project import (
    get_project,
    list_projects,
    create_project,
    delete_project,
)
from ragapp.workspace.manager import set_current_project
from ragapp.agent.loop import run_agent
from ragapp.tools import build_default_tools
from ragapp.chat_sessions import ChatSessionStore
from ragapp.interfaces.streamlit_app.file_manager import (
    render_file_manager,
    _prepare_html_preview,
)
from ragapp.core.drafts import DraftManager
from ragapp.core.approval import ApprovalEngine
from ragapp.core.instructions import InstructionStore
from ragapp.core.vcs import VCSManager
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
# Project helpers
# ===========================================================================

PROVIDERS = [
    "gemini",
    "openrouter",
    "openai",
    "anthropic",
    "azure",
]

DEFAULT_MODELS = {
    "gemini": "gemini-3.5-flash-lite",
    "openrouter": "liquid/lfm-2.5-2.6b:free",
    "openai": "gpt-5.6-luna",
    "anthropic": "",
    "azure": "gpt-5.6-luna",
}


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
        key="create_project_provider",
    )

    default_model = DEFAULT_MODELS.get(
        provider,
        "",
    )

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
        "Model",
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

    api_key = st.text_input(
        f"{provider.title()} API key",
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

        if api_key.strip():
            provider_cfg.setdefault(
                "api_keys",
                {},
            )[provider] = api_key.strip()

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

    for key in (
        "create_project_name",
        "create_project_provider",
        "create_project_model",
        "create_project_api_key",
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

if st.session_state.project_id not in projects:
    st.session_state.project_id = (
        projects[0]
    )

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
    current_id = st.session_state.get(
        "chat_session_id",
    )

    session = (
        sessions.load(current_id)
        if current_id
        else None
    )

    if session is None:
        session = session_list[0]

st.session_state.chat_session_id = session["id"]

st.session_state.messages = session.get(
    "messages",
    [],
)

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

    st.markdown(
        "### Project"
    )

    project_col, delete_project_col = st.columns(
        [5, 1]
    )

    with project_col:
        selected_project = st.selectbox(
            "Project",
            projects,
            index=projects.index(
                st.session_state.project_id
            ),
            label_visibility="collapsed",
            key="project_selector",
        )

    with delete_project_col:
        if st.button(
            "🗑",
            key="delete_selected_project",
            help="Delete selected project",
            use_container_width=True,
        ):
            st.session_state[
                "confirm_delete_project"
            ] = True

    if st.button(
        "＋ New project",
        use_container_width=True,
    ):
        _new_project_dialog()

    if (
        selected_project
        != st.session_state.project_id
    ):
        st.session_state.project_id = (
            selected_project
        )

        st.session_state.pop(
            "chat_session_id",
            None,
        )

        st.session_state.messages = []

        st.rerun()

    if st.session_state.get(
        "confirm_delete_project"
    ):
        st.warning(
            f"Delete project "
            f"'{st.session_state.project_id}' "
            f"permanently?"
        )

        dp1, dp2 = st.columns(2)

        with dp1:
            if st.button(
                "Delete permanently",
                key="confirm_project_delete",
                type="primary",
                use_container_width=True,
            ):
                project_to_delete = (
                    st.session_state.project_id
                )

                delete_project(
                    username,
                    project_to_delete,
                )

                remaining = list_projects(
                    username,
                )

                if not remaining:
                    create_project(
                        username,
                        "default",
                    )

                    remaining = [
                        "default"
                    ]

                st.session_state.project_id = (
                    remaining[0]
                )

                st.session_state.pop(
                    "chat_session_id",
                    None,
                )

                st.session_state.messages = []

                st.session_state.pop(
                    "confirm_delete_project",
                    None,
                )

                st.rerun()

        with dp2:
            if st.button(
                "Cancel",
                key="cancel_project_delete",
                use_container_width=True,
            ):
                st.session_state.pop(
                    "confirm_delete_project",
                    None,
                )

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

    st.markdown(
        "### Project status"
    )

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

    if st.button(
        "Log out",
        use_container_width=True,
    ):
        st.session_state.authenticated = False
        st.session_state.messages = []
        st.rerun()

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

    if suffix == ".pdf":
        encoded = base64.b64encode(raw).decode("ascii")
        st.markdown(
            f'<iframe src="data:application/pdf;base64,{encoded}" '
            'width="100%" height="650" style="border:0;border-radius:6px;">'
            '</iframe>',
            unsafe_allow_html=True,
        )
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
    """
    Convert the runtime execution trace into JSON/session-safe data.

    The trace is stored inside the assistant message so it survives:
      - Streamlit reruns
      - session switching
      - application refreshes
      - loading an existing ChatSessionStore session
    """
    result = []

    for call in calls or []:
        if not isinstance(call, dict):
            continue

        result.append(
            {
                "tool": call.get("tool"),
                "args": call.get("args"),
                "result": call.get("result"),
            }
        )

    return result


def _render_tool_calls(tool_calls):
    """
    Render a persisted execution trace underneath the assistant response.
    """
    if not tool_calls:
        return

    with st.expander(
        f"Execution trace · {len(tool_calls)} tool calls",
        expanded=False,
    ):
        for index, call in enumerate(tool_calls, start=1):
            tool_name = call.get(
                "tool",
                "unknown",
            )

            st.markdown(
                f"**Tool call {index}: `{tool_name}`**"
            )

            args = call.get(
                "args",
            )

            if args is not None:
                st.markdown("**Arguments**")
                st.json(args)

            result = call.get(
                "result",
            )

            if result is not None:
                st.markdown("**Result**")

                if isinstance(
                    result,
                    (dict, list),
                ):
                    st.json(result)
                else:
                    st.code(
                        str(result)
                    )


# ===========================================================================
# Chat
# ===========================================================================

if nav_section == "chat":
    preview_enabled = st.toggle(
        "Show file preview",
        value=False,
        key="chat_preview_enabled",
        help=(
            "Show or hide the optional file preview panel."
        ),
    )

    chat_col = st.container()

    with chat_col:
        # ------------------------------------------------------------------
        # ONE bounded scrolling transcript.
        # ------------------------------------------------------------------

        chat_box = st.container(
            height=350,
            border=False,
        )

        with chat_box:
            if not st.session_state.messages:
                st.markdown(
                    "Start a task. You can ask the agent to "
                    "create, inspect, edit, move, copy, or delete "
                    "project files without uploading anything first."
                )

            for m in st.session_state.messages:
                with st.chat_message(
                    m["role"]
                ):
                    st.markdown(
                        m.get(
                            "content",
                            "",
                        )
                    )

                    # ------------------------------------------------------
                    # Persisted tool trace.
                    #
                    # Existing messages without tool_calls simply render
                    # normally.
                    # ------------------------------------------------------
                    _render_tool_calls(
                        m.get(
                            "tool_calls",
                            [],
                        )
                    )

        # ------------------------------------------------------------------
        # Chat input deliberately remains OUTSIDE the scrolling container.
        # ------------------------------------------------------------------

        prompt = st.chat_input(
            "Ask a question or give the agent a task…"
        )

        if prompt:
            # --------------------------------------------------------------
            # Persist the user message immediately.
            # --------------------------------------------------------------

            user_message = {
                "role": "user",
                "content": prompt,
            }

            st.session_state.messages.append(
                user_message
            )

            session = (
                sessions.load(
                    st.session_state.chat_session_id
                )
                or session
            )

            session["messages"] = (
                st.session_state.messages
            )

            if session.get("title") == "New chat":
                session["title"] = (
                    prompt[:60].strip()
                    or "New chat"
                )

            sessions.save(
                session
            )

            # --------------------------------------------------------------
            # Current turn is rendered into chat_box too.
            # --------------------------------------------------------------

            with chat_box:
                with st.chat_message(
                    "user"
                ):
                    st.markdown(
                        prompt
                    )

                with st.chat_message(
                    "assistant"
                ):
                    placeholder = st.empty()

                    stream_state = {
                        "rendered": "",
                        "plan": None,
                        "done": [],
                    }

                    def _render_plan():
                        lines = [
                            (
                                f"- "
                                f"{'✅' if i < len(stream_state.get('done', [])) else '⏳'} "
                                f"{title}"
                            )
                            for i, title in enumerate(
                                stream_state["plan"]
                            )
                        ]

                        return (
                            "**Planned sections:**\n"
                            + "\n".join(lines)
                        )

                    def _on_section(event):
                        if event["type"] == "plan":
                            stream_state["plan"] = (
                                event["sections"]
                            )

                            stream_state["done"] = []

                            placeholder.markdown(
                                _render_plan()
                            )

                        elif event["type"] == "section":
                            if (
                                stream_state["plan"]
                                is not None
                            ):
                                stream_state["done"].append(
                                    event["title"]
                                )

                            stream_state["rendered"] += (
                                (
                                    "\n\n"
                                    if stream_state["rendered"]
                                    else ""
                                )
                                + event["content"]
                            )

                            placeholder.markdown(
                                stream_state["rendered"]
                            )

                    calls = []
                    drafts = []
                    answer = ""

                    try:
                        with st.spinner(
                            "Executing…"
                        ):
                            set_current_project(
                                store
                            )

                            tools = build_default_tools(
                                username,
                                store,
                                include_cognition=True,
                            )

                            answer, calls, drafts = run_agent(
                                st.session_state.messages,
                                tools,
                                store,
                                store.project_id,
                                session_id=(
                                    st.session_state.chat_session_id
                                ),
                                on_section=_on_section,
                            )

                    except Exception as exc:
                        # --------------------------------------------------
                        # IMPORTANT:
                        # Preserve the failure in the assistant message
                        # instead of letting it appear detached below the
                        # chat input.
                        # --------------------------------------------------

                        error_text = (
                            "The agent encountered an error while "
                            "executing this request."
                        )

                        if stream_state["rendered"]:
                            answer = (
                                stream_state["rendered"]
                                + "\n\n"
                                + error_text
                            )
                        else:
                            answer = error_text

                        calls = calls or []

                        tool_calls = _serialise_tool_calls(
                            calls
                        )

                        if exc:
                            tool_calls.append(
                                {
                                    "tool": "agent_error",
                                    "args": {},
                                    "result": str(exc),
                                }
                            )

                        placeholder.markdown(
                            answer
                        )

                        _render_tool_calls(
                            tool_calls
                        )

                    else:
                        final_text = (
                            answer.strip()
                            if answer
                            and answer.strip()
                            else (
                                stream_state["rendered"]
                                or (
                                    _render_plan()
                                    + "\n\n⚠️ No sections were "
                                    "delivered before the model stopped."
                                    if stream_state.get("plan")
                                    else
                                    "⚠️ The agent produced no response "
                                    "for this turn. Try again."
                                )
                            )
                        )

                        placeholder.markdown(
                            final_text
                        )

                        answer = final_text

                        # --------------------------------------------------
                        # Convert the runtime trace into persistent data.
                        # --------------------------------------------------

                        tool_calls = _serialise_tool_calls(
                            calls
                        )

                        # --------------------------------------------------
                        # Show the same trace immediately for the current
                        # response.
                        # --------------------------------------------------

                        _render_tool_calls(
                            tool_calls
                        )

                        if drafts:
                            ids = ", ".join(
                                f"`{d['id'][:8]}`"
                                for d in drafts
                            )

                            st.info(
                                f"{'Draft' if len(drafts) == 1 else 'Drafts'} "
                                f"created: {ids}. Review "
                                f"{'it' if len(drafts) == 1 else 'them'} "
                                "in the Review tab before "
                                f"{'it' if len(drafts) == 1 else 'they'} "
                                "can affect /source."
                            )

                    # ------------------------------------------------------
                    # CRITICAL:
                    # Store tool_calls WITH the assistant message.
                    # ------------------------------------------------------

                    assistant_message = {
                        "role": "assistant",
                        "content": answer,
                        "tool_calls": tool_calls,
                    }

                    st.session_state.messages.append(
                        assistant_message
                    )

                    # ------------------------------------------------------
                    # Persist the complete message, including execution
                    # trace.
                    # ------------------------------------------------------

                    session["messages"] = (
                        st.session_state.messages
                    )

                    sessions.save(
                        session
                    )

            # --------------------------------------------------------------
            # Rerender so the newly-created assistant message becomes part
            # of the persistent transcript on the next Streamlit run.
            # --------------------------------------------------------------

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
    st.subheader(
        "Persistent cognition"
    )

    state = store.state_map()

    a, b, c = st.columns(3)

    a.metric(
        "Entities",
        len(
            state.get(
                "entities",
                {}
            )
        ),
    )

    b.metric(
        "Version",
        state.get(
            "current_version",
            0,
        ),
    )

    c.metric(
        "Relationships",
        len(
            store.relationships()
        ),
    )

    if st.session_state.get(
        "compile_success_msg"
    ):
        st.success(
            st.session_state.pop(
                "compile_success_msg"
            )
        )

    elif state.get("compiled"):
        st.success(
            "Cognition has been compiled from project artifacts."
        )

    else:
        st.info(
            "Cognition is ready but has not been compiled "
            "from project artifacts. This does not block "
            "chat or file work."
        )

    available = list_available_files(
        store
    )

    if not available:
        st.caption(
            "No files in Source or Workspace yet — "
            "add some under the Files tab first."
        )

    else:
        labels = [
            (
                f"[{f['area']}] "
                f"{f['path']}  ·  {f['size']}"
            )
            for f in available
        ]

        label_to_key = {
            lbl: (
                f["area"],
                f["path"],
            )
            for lbl, f in zip(
                labels,
                available,
            )
        }

        chosen_labels = st.multiselect(
            "Files to compile",
            labels,
            default=labels,
            key="compile_file_picker",
        )

        chosen = [
            label_to_key[l]
            for l in chosen_labels
        ]

        st.caption(
            f"{len(chosen)} of {len(available)} "
            "file(s) selected. Unselected files keep "
            "whatever cognition was already compiled "
            "from them — only the selected files are "
            "(re)extracted and merged in this run."
        )

        if st.button(
            "Compile selected files",
            type="primary",
            disabled=not chosen,
        ):
            try:
                progress_box = st.empty()
                status_box = st.empty()

                progress = st.progress(
                    0
                )

                status = st.empty()

                def on_progress(
                    done,
                    total,
                ):
                    progress.progress(
                        done / total
                        if total
                        else 0
                    )

                    status.info(
                        f"Compiling batch {done}/{total}"
                    )

                status.info(
                    "Compilation started — preparing source..."
                )

                result = compile_project(
                    store,
                    selected_files=chosen,
                    progress_callback=on_progress,
                )

                progress_box.progress(
                    1.0
                )

                status_box.success(
                    "Compilation complete."
                )

                st.session_state[
                    "compile_success_msg"
                ] = (
                    f"Processed "
                    f"{result.get('source_files', 0)} "
                    "source + "
                    f"{result.get('workspace_files', 0)} "
                    "workspace file(s) this run; "
                    f"{result.get('entities', 0)} "
                    "total entities now in cognition."
                )

                st.rerun()

            except Exception as exc:
                st.error(
                    str(exc)
                )

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
    )

    if provider != old_provider:
        if provider == "openrouter":
            prov["model"] = (
                "liquid/lfm-2.5-2.6b:free"
            )

        elif provider == "gemini":
            prov["model"] = (
                "gemini-3.5-flash-lite"
            )

        else:
            prov["model"] = ""

    prov["name"] = provider

    prov["model"] = st.text_input(
        "Model",
        prov.get(
            "model",
            "",
        ),
        help=(
            "This model is stored with the project "
            "and must belong to the selected provider."
        ),
    )

    key = st.text_input(
        f"{provider.title()} API key",
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
        PROVIDERS,
        default=[
            x
            for x in prov.get(
                "fallback",
                [],
            )
            if x in PROVIDERS
        ],
    )

    prov["fallback"] = fallback

    limits["max_agent_steps"] = st.number_input(
        "Max agent steps",
        1,
        100,
        int(
            limits.get(
                "max_agent_steps",
                12,
            )
        ),
    )

    limits["transcript_turns"] = st.number_input(
        "Transcript clearing turn threshold",
        5,
        1000,
        int(
            limits.get(
                "transcript_turns",
                40,
            )
        ),
    )

    limits["transcript_chars"] = st.number_input(
        "Transcript clearing character threshold",
        1000,
        1000000,
        int(
            limits.get(
                "transcript_chars",
                80000,
            )
        ),
    )

    limits["requests_per_minute"] = st.number_input(
        "Provider requests/minute",
        1,
        1000,
        int(
            limits.get(
                "requests_per_minute",
                15,
            )
        ),
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
            )[provider] = key

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