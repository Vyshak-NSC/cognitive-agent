"""Streamlit UI. A thin shell over the service.

Run:  uv run cpa-ui        (opens http://localhost:8501)
"""

from __future__ import annotations

import streamlit as st

from cpa import __version__
from cpa.providers import Message, ProviderError
from cpa.service import ChatRequest, get_service

st.set_page_config(page_title=f"CPA v{__version__}", page_icon="💬")
service = get_service()
infos = {info.id: info for info in service.providers()}

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Provider")
    provider_id = st.selectbox(
        "Provider",
        list(infos),
        index=list(infos).index(service.settings.default_provider),
        format_func=lambda pid: f"{'✅' if infos[pid].configured else '⚠️'} {infos[pid].label}",
    )
    info = infos[provider_id]
    if not info.configured:
        st.warning("Not set up. Add to .env: " + ", ".join(info.missing))
    model = st.text_input(info.model_label, value=info.model, key=f"model_{provider_id}")
    system = st.text_area("System prompt (optional)", key="system")
    use_temperature = st.checkbox("Set temperature")
    temperature = st.slider("Temperature", 0.0, 2.0, 1.0, 0.1) if use_temperature else None
    if st.button("Clear chat"):
        st.session_state["turns"] = []
        st.rerun()
    st.caption(f"v{__version__}")

# ------------------------------------------------------------------- chat
turns: list[dict] = st.session_state.setdefault("turns", [])  # {"message": Message, "note": str}

st.title("CPA")
for turn in turns:
    with st.chat_message(turn["message"].role):
        st.markdown(turn["message"].content or "")
        if turn.get("note"):
            st.caption(turn["note"])

if prompt := st.chat_input("Message"):
    with st.chat_message("user"):
        st.markdown(prompt)
    history = [t["message"] for t in turns] + [Message("user", prompt)]
    try:
        with st.spinner("Thinking..."):
            result = service.chat(
                ChatRequest(
                    messages=history,
                    provider=provider_id,
                    model=model,
                    system=system or None,
                    temperature=temperature,
                )
            )
    except ProviderError as exc:
        st.error(f"Message not sent. {exc}")  # nothing is stored, so you can send it again
    else:
        usage = result.usage.total_tokens if result.usage else None
        note = f"{result.provider} / {result.model}" + (f" / {usage} tokens" if usage else "")
        turns.append({"message": Message("user", prompt)})
        turns.append({"message": result.message, "note": note})
        st.rerun()
