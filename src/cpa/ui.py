"""Streamlit UI. A thin shell over the service.

Mirrors the React app in web/: same header and navigation, same Settings page, same chat view.
Run:  uv run cpa-ui        (opens http://localhost:8501)
"""

from __future__ import annotations

import streamlit as st

from cpa import __version__
from cpa.providers import ConfigError, Message, ProviderError
from cpa.service import ChatRequest, get_service

NAV = {
    "chat": "Chat",
    "files": "Files",
    "cognition": "Cognition",
    "review": "Review",
    "history": "History",
    "settings": "Settings",
}

st.set_page_config(page_title="CPA", page_icon="💬", layout="wide")
service = get_service()
infos = {info.id: info for info in service.providers()}
ss = st.session_state

# ------------------------------------------------------------------ state
# Plain values live in session_state under fixed names. Widgets that edit them use the same
# name as their `key`, so the widget and the stored value are one thing.
default_id = next((i.id for i in infos.values() if i.default), next(iter(infos)))
ss.setdefault("section", "chat")
ss.setdefault("provider_id", default_id)
ss.setdefault("system", "")
ss.setdefault("use_temperature", False)
ss.setdefault("temperature", 1.0)
ss.setdefault("chats", [{"id": 1, "title": "New chat", "turns": []}])
ss.setdefault("active_chat_id", 1)
ss.setdefault("next_chat_id", 2)
for pid, info in infos.items():
    ss.setdefault(f"model_{pid}", info.model)  # one model box per provider

# Streamlit forgets a widget's value in any run where the widget is not drawn (the Settings
# widgets while you are on the Chat page). Saving each value again at the top of the run keeps it.
KEPT = ["section", "provider_id", "system", "use_temperature", "temperature"]
for key in [*KEPT, *(f"model_{pid}" for pid in infos)]:
    ss[key] = ss[key]


def active_chat() -> dict:
    return next(chat for chat in ss.chats if chat["id"] == ss.active_chat_id)


def provider_name(pid: str) -> str:
    return infos[pid].label if pid in infos else pid


# ----------------------------------------------------------------- header
def render_header() -> None:
    brand, nav, account = st.columns([3, 6, 3], vertical_alignment="center")
    brand.markdown("**Cognitive Persistence**  \n:gray[Persistent project cognition]")
    nav.radio(
        "Main navigation",
        list(NAV),
        key="section",
        format_func=NAV.get,
        horizontal=True,
        label_visibility="collapsed",
    )
    with account:
        project, user = st.columns(2, vertical_alignment="center")
        project.selectbox("Project", ["n"], label_visibility="collapsed")
        user.caption("👤 test")
    st.caption("0 files · 0 entities")


# --------------------------------------------------------------- settings
def render_settings() -> None:
    tabs = st.tabs(["⚙ Settings", "Providers", "About", "Logs"])
    for tab in tabs[1:]:
        with tab:
            st.caption("Coming soon.")
    with tabs[0]:
        with st.container(border=True):
            st.subheader("Model configuration")
            st.selectbox(
                "Provider",
                list(infos),
                key="provider_id",
                format_func=lambda pid: (
                    infos[pid].label + ("" if infos[pid].configured else " · Not configured")
                ),
            )
            info = infos[ss.provider_id]
            st.text_input(info.model_label, key=f"model_{info.id}")
            if not info.configured:
                st.warning(f"{info.label} needs configuration: {', '.join(info.missing)}")
        with st.container(border=True):
            st.subheader("Response behaviour")
            st.text_area(
                "System prompt", key="system", placeholder="You are a helpful AI assistant."
            )
            st.caption("This prompt is sent with every request.")
            st.checkbox("Set temperature", key="use_temperature")
            if ss.use_temperature:
                st.slider("Temperature", 0.0, 2.0, step=0.1, key="temperature")
            st.caption("Lower values are more deterministic. Higher values are more creative.")
            
        with st.container(border=True):
            st.subheader("Interface")
            st.selectbox("Theme", ["Dark (default)"])
        st.caption(f"CPA v{__version__}")


# ----------------------------------------------------------- placeholders
def render_placeholder(section: str) -> None:
    with st.container(border=True):
        st.subheader(f"✧ {NAV[section]}")
        st.write(
            "This area is a placeholder for a future v11 feature. "
            "Existing chat and model settings remain available."
        )


# ------------------------------------------------------------------- chat
def count(value: int | None) -> str:
    return "Unavailable" if value is None else str(value)


def render_turn(turn: dict) -> None:
    message = turn["message"]
    is_reply = message.role == "assistant"
    with st.chat_message(message.role):
        st.caption("CPA" if is_reply else "You")
        if message.content:
            st.markdown(message.content)
        else:
            st.caption("The model asked to call a tool. Tools aren't enabled in this client yet.")
        meta = turn.get("meta")
        if meta:
            with st.popover("ⓘ"):
                st.write(f"Provider: {provider_name(meta['provider']) or 'Unknown'}")
                st.write(f"Model: {meta['model'] or 'Unknown'}")
                st.write(f"Input Tokens: {count(meta['prompt_tokens'])}")
                st.write(f"Output Tokens: {count(meta['completion_tokens'])}")
                st.write(f"Total Tokens: {count(meta['total_tokens'])}")


def show_failure(exc: ProviderError, label: str) -> None:
    text = f"**Message not sent**\n\n{exc}"
    if isinstance(exc, ConfigError):
        text += f"\n\nCheck the {label} entries in your .env file, then send the message again."
    st.error(text)  # nothing is stored, so you can send it again


def render_chat() -> None:
    chat = active_chat()
    info = infos[ss.provider_id]
    if not chat["turns"]:
        with st.container(border=True):
            st.subheader("ϟ Start a task.")
            st.write(
                "You can ask the agent to create, inspect, edit, move, copy, or delete "
                "project files without uploading anything first."
            )
    for turn in chat["turns"]:
        render_turn(turn)

    if not info.configured:
        st.warning(f"{info.label} isn't set up. Add {', '.join(info.missing)} to .env.")
    prompt = st.chat_input(f"Message {info.label}", disabled=not info.configured)
    if not prompt:
        return

    with st.chat_message("user"):
        st.caption("You")
        st.markdown(prompt)
    history = [turn["message"] for turn in chat["turns"]] + [Message("user", prompt)]
    try:
        with st.spinner(f"Waiting for {info.label}"):
            result = service.chat(
                ChatRequest(
                    messages=history,
                    provider=info.id,
                    model=ss[f"model_{info.id}"].strip() or None,
                    system=ss.system.strip() or None,
                    temperature=ss.temperature if ss.use_temperature else None,
                )
            )
    except ProviderError as exc:
        show_failure(exc, info.label)
        return

    usage = result.usage
    chat["turns"].append({"message": Message("user", prompt)})
    chat["turns"].append(
        {
            "message": result.message,
            "meta": {
                "provider": result.provider,
                "model": result.model,
                "prompt_tokens": usage.prompt_tokens if usage else None,
                "completion_tokens": usage.completion_tokens if usage else None,
                "total_tokens": usage.total_tokens if usage else None,
            },
        }
    )
    if chat["title"] == "New chat":
        chat["title"] = " ".join(prompt.split())[:30]
    st.rerun()


# ------------------------------------------------------------------- page
render_header()
if ss.section == "settings":
    render_settings()
elif ss.section == "chat":
    render_chat()
else:
    render_placeholder(ss.section)
