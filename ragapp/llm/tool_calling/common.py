"""Pieces shared by every tool-calling adapter.

The transcript stores messages in one canonical, OpenAI-shaped form
(``assistant.tool_calls`` and ``role="tool"`` results). The wire modules
(`openai_wire`, `anthropic_wire`) translate that form to each vendor API, so a
stored chat can be continued with any provider.
"""
from __future__ import annotations

import json
import os
import re
import threading

from ragapp.config import get_api_key, load_project_config
from ragapp.llm.registry import PROVIDER_SPECS

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)

_CLIENTS: dict[tuple, object] = {}
_CLIENTS_LOCK = threading.Lock()


class EmptyResponseError(RuntimeError):
    """The model returned neither text nor tool calls.

    The agent loop recognises this exception by class name.
    """


# ------------------------------------------------------------ credentials

def credential(store, provider_id):
    """API key for a provider: its env var, else the project's key slot."""
    spec = PROVIDER_SPECS[provider_id]
    env_key = os.getenv(spec["key_env"]) if spec["key_env"] else None
    return (env_key or get_api_key(store, spec["key_slot"]) or "").strip()


def configured_model(store, provider_id):
    """The project's model, else the provider's registry default."""
    model = load_project_config(store).get("provider", {}).get("model")
    return (model or PROVIDER_SPECS[provider_id]["default_model"]).strip()


def cached_client(cache_key, factory):
    """One SDK client per key, so the HTTP connection pool survives across steps."""
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(cache_key)
        if client is None:
            client = _CLIENTS[cache_key] = factory()
    return client


# ----------------------------------------------------------- tool calls

def get(obj, key, default=None):
    """Read a field from either a dict or an SDK object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def args_json(args):
    if args is None:
        return "{}"
    if isinstance(args, str):
        return args
    if isinstance(args, (dict, list)):
        return json.dumps(args, ensure_ascii=False)
    return str(args)


def tool_call(call_id, name, arguments):
    """The single place that builds the canonical tool-call shape."""
    return {
        "id": str(call_id or f"call_{name}"),
        "type": "function",
        "function": {"name": str(name), "arguments": args_json(arguments)},
    }


def canonical_tool_call(call):
    if call is None:
        return None
    fn = get(call, "function") or {}
    name = get(fn, "name") or get(call, "name")
    if not name:
        return None
    args = get(fn, "arguments", get(call, "arguments", "{}"))
    return tool_call(get(call, "id"), name, args)


def parse_args(raw):
    """(args_dict, error). Tolerates code fences; never raises."""
    if isinstance(raw, dict):
        return raw, None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}, None
    text = raw if isinstance(raw, str) else str(raw)
    for candidate in (text, FENCE.sub("", text)):
        try:
            value = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        return (value if isinstance(value, dict) else {}), None
    return {}, f"Tool arguments were not valid JSON: {text[:200]!r}"


# ------------------------------------------------------ canonical messages

def to_provider_contents(contents, store=None):
    """Internal messages -> canonical messages (empty turns are dropped)."""
    out = []
    for item in contents:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "user")
        content = item.get("content", "")

        if role in ("assistant", "model"):
            calls = [
                c
                for c in map(canonical_tool_call, item.get("tool_calls") or [])
                if c is not None
            ]
            if calls:
                out.append(
                    {"role": "assistant", "content": content or None, "tool_calls": calls}
                )
            elif content:
                out.append({"role": "assistant", "content": content})

        elif role == "tool":
            call_id = item.get("tool_call_id") or item.get("id")
            if call_id:
                out.append(
                    {"role": "tool", "tool_call_id": str(call_id), "content": str(content)}
                )
            elif content:  # orphaned tool result: can only be sent as user text
                out.append({"role": "user", "content": str(content)})

        elif role == "system":
            if content:
                out.append({"role": "system", "content": content})

        elif content:
            out.append({"role": "user", "content": content})
    return out


def build_assistant_message(text, tool_calls):
    """Assistant message to append to history after a step."""
    if not tool_calls:
        return {"role": "assistant", "content": text}
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            tool_call(c.get("id"), c["name"], c["args"]) for c in tool_calls
        ],
    }


def build_function_response_content(name, result, call_id=None):
    """Tool result message for the next agent step."""
    return {
        "role": "tool",
        "tool_call_id": call_id or name,
        "content": json.dumps(result, ensure_ascii=False, default=str),
    }


# ------------------------------------------------------------------- misc

def usage(prompt, completion, total=None):
    if prompt is None and completion is None and total is None:
        return None
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def clean_json_text(text):
    """Return text unchanged if it parses; else try to salvage fenced/wrapped JSON."""
    if not text:
        return text
    try:
        json.loads(text)
        return text
    except ValueError:
        pass
    stripped = FENCE.sub("", text).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    for candidate in (stripped, stripped[start : end + 1] if 0 <= start < end else ""):
        try:
            json.loads(candidate)
            return candidate
        except ValueError:
            continue
    return text  # let the caller's own repair/retry logic handle it
