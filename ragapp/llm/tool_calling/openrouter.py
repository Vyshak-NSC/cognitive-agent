"""Direct OpenRouter SDK adapter.

Optional keys under ``provider:`` in the project config (all have defaults):

    timeout_s            per-request timeout in seconds (default 120)
    retry_budget_s       max total time the SDK spends retrying 429/5xx (default 45)
    fallback_models      list of OpenRouter model ids tried if the primary fails
    require_parameters   true = only route to backends that support tools /
                         JSON mode instead of silently ignoring them (default false)
    routing              extra OpenRouter provider-routing preferences (dict)
    temperature          sampling temperature for agent steps (default: model's own)
    site_url / site_name attribution headers
"""
from __future__ import annotations

import json
import os
import re
import threading
from types import SimpleNamespace
from typing import Any

from openrouter import OpenRouter
from openrouter.utils import BackoffStrategy, RetryConfig

from ragapp.config import get_api_keys, load_project_config

DEFAULT_OPENROUTER_MODEL = "openrouter/free"
DEFAULT_TIMEOUT_S = 120
DEFAULT_RETRY_BUDGET_S = 45

_RETRY_STATUS = ["500", "502", "503", "504"]  # 429 added for the last key only
_ROTATE_STATUS = {401, 402, 429}  # bad key / no credits / rate limited -> next key

_CLIENTS: dict[tuple, OpenRouter] = {}
_CLIENTS_LOCK = threading.Lock()


class EmptyResponseError(RuntimeError):
    """Raised when the model returns neither text nor tool calls.

    The agent loop recognises this exception by class name and turns it into a
    readable message instead of an empty assistant reply.
    """


# ---------------------------------------------------------------- settings

def _settings(store):
    cfg = load_project_config(store)
    provider = cfg.setdefault("provider", {})
    configured = os.getenv("OPENROUTER_MODEL") or provider.get("model")

    # Never send another provider's model name (e.g. gemini-*) to OpenRouter.
    if (
        not configured
        or configured.startswith("gemini")
        or ("/" not in configured and configured != DEFAULT_OPENROUTER_MODEL)
    ):
        return cfg, DEFAULT_OPENROUTER_MODEL
    return cfg, configured


def _no_key_error():
    return RuntimeError(
        "No OpenRouter API key configured. "
        "Add it in Project Settings or set OPENROUTER_API_KEY."
    )


def _client_for(key, pcfg, retry_429=True):
    """One cached SDK client per (key, headers, timeout, retry policy).

    Re-using the client keeps the HTTP connection pool (TLS + keep-alive) alive
    across agent steps instead of rebuilding it on every call.
    """
    referer = pcfg.get("site_url") or None
    title = pcfg.get("site_name") or None
    timeout_s = float(pcfg.get("timeout_s") or DEFAULT_TIMEOUT_S)
    budget_s = float(pcfg.get("retry_budget_s") or DEFAULT_RETRY_BUDGET_S)
    cache_key = (key, referer, title, timeout_s, budget_s, retry_429)

    with _CLIENTS_LOCK:
        client = _CLIENTS.get(cache_key)
        if client is None:
            kwargs: dict[str, Any] = {
                "api_key": key,
                "timeout_ms": int(timeout_s * 1000),
                # SDK-native backoff; it also honours Retry-After headers.
                "retry_config": RetryConfig(
                    "backoff",
                    BackoffStrategy(500, 8000, 2.0, int(budget_s * 1000)),
                    True,
                    _RETRY_STATUS + (["429"] if retry_429 else []),
                ),
            }
            if referer:
                kwargs["http_referer"] = referer
            if title:
                kwargs["x_open_router_title"] = title
            client = _CLIENTS[cache_key] = OpenRouter(**kwargs)
    return client


class _NS(SimpleNamespace):
    """Attribute-style view of a raw JSON response (same access pattern as the SDK)."""

    def model_dump(self, **_):
        return _plain(self)


def _ns(value):
    if isinstance(value, dict):
        return _NS(**{k: _ns(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_ns(v) for v in value]
    return value


def _plain(value):
    if isinstance(value, _NS):
        return {k: _plain(v) for k, v in vars(value).items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _lenient_response(exc):
    """Salvage a 200 response the SDK's strict schema rejected.

    Routed backends (especially free ones) sometimes omit or null fields the SDK
    marks as required, e.g. an empty tool-call id. The payload is usable, so
    parse it directly instead of failing the whole agent step.
    """
    if type(exc).__name__ != "ResponseValidationError":
        return None
    try:
        data = json.loads(exc.body)
    except (TypeError, ValueError, AttributeError):
        return None
    return _ns(data) if isinstance(data, dict) else None


def _client(store):
    """Client for the first configured key (kept for backward compatibility)."""
    keys = get_api_keys(store, "openrouter")
    if not keys:
        raise _no_key_error()
    return _client_for(keys[0], load_project_config(store).get("provider", {}))


def _send(store, pcfg, **kwargs):
    """chat.send with key rotation.

    On 401/402/429 the next configured key is tried immediately; only the last
    key waits out a 429 with backoff.
    """
    keys = get_api_keys(store, "openrouter")
    if not keys:
        raise _no_key_error()

    for i, key in enumerate(keys):
        last = i == len(keys) - 1
        try:
            return _client_for(key, pcfg, retry_429=last).chat.send(**kwargs)
        except Exception as exc:
            lenient = _lenient_response(exc)
            if lenient is not None:
                return lenient
            if not last and getattr(exc, "status_code", None) in _ROTATE_STATUS:
                continue
            raise


def _target(model, pcfg):
    """`model`, or `models=[...]` (server-side fallback chain) when configured."""
    fallbacks = [m for m in (pcfg.get("fallback_models") or []) if isinstance(m, str)]
    if fallbacks:
        return {"models": [model, *[m for m in fallbacks if m != model]]}
    return {"model": model}


def _routing(pcfg, needs_params):
    prefs: dict[str, Any] = {}
    if needs_params and pcfg.get("require_parameters", False):
        prefs["require_parameters"] = True
    prefs.update(pcfg.get("routing") or {})
    return {"provider": prefs} if prefs else {}


# ------------------------------------------------------- tool-call helpers

def _get(obj, key, default=None):
    """Read a field from either a dict or an SDK object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _args_json(args):
    """Tool-call arguments as a JSON string."""
    if args is None:
        return "{}"
    if isinstance(args, str):
        return args
    if isinstance(args, (dict, list)):
        return json.dumps(args, ensure_ascii=False)
    return str(args)


def _tool_call(call_id, name, arguments):
    """The single place that builds the strict OpenAI/OpenRouter tool-call shape."""
    return {
        "id": str(call_id or f"call_{name}"),
        "type": "function",
        "function": {"name": str(name), "arguments": _args_json(arguments)},
    }


def _canonical_tool_call(call):
    """Normalize a tool call (SDK object or dict) to the strict shape."""
    if call is None:
        return None
    fn = _get(call, "function") or {}
    name = _get(fn, "name") or _get(call, "name")
    if not name:
        return None
    args = _get(fn, "arguments", _get(call, "arguments", "{}"))
    return _tool_call(_get(call, "id"), name, args)


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)


def _parse_args(raw):
    """(args_dict, error). Tolerates code fences; never raises."""
    if isinstance(raw, dict):
        return raw, None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}, None
    text = raw if isinstance(raw, str) else str(raw)
    for candidate in (text, _FENCE.sub("", text)):
        try:
            value = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        return (value if isinstance(value, dict) else {}), None
    return {}, f"tool arguments were not valid JSON: {text[:200]!r}"


# ------------------------------------------------- request-side conversion

def to_provider_contents(contents, store=None):
    """Convert internal messages into OpenRouter messages."""
    out = []
    for item in contents:
        if not isinstance(item, dict):
            continue

        role = item.get("role", "user")
        content = item.get("content", "")

        if role in ("assistant", "model"):
            calls = [
                c
                for c in map(_canonical_tool_call, item.get("tool_calls") or [])
                if c is not None
            ]
            if calls:
                msg = {"role": "assistant", "content": None, "tool_calls": calls}
                if item.get("reasoning_details"):
                    msg["reasoning_details"] = item["reasoning_details"]
                out.append(msg)
            elif content:  # empty assistant turns are rejected by some backends
                out.append({"role": "assistant", "content": content})

        elif role == "tool":
            call_id = item.get("tool_call_id") or item.get("id")
            if call_id:
                out.append(
                    {"role": "tool", "tool_call_id": str(call_id), "content": content}
                )
            else:
                # Orphaned tool result: can't be sent as a tool message.
                out.append({"role": "user", "content": content})

        elif role == "system":
            out.append({"role": "system", "content": content})

        elif content:
            out.append({"role": "user", "content": content})
    return out


def to_openrouter_tools(declarations):
    """Convert internal tool declarations to OpenRouter tool format."""
    return [
        {
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d.get("description", ""),
                "parameters": d.get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            },
        }
        for d in declarations
    ]


# ------------------------------------------------ response-side extraction

def _extract_message(response):
    if not getattr(response, "choices", None):
        detail = getattr(response, "error", None)
        raise RuntimeError(
            "OpenRouter returned no choices."
            + (f" Provider error: {_plain(detail)}" if detail else "")
        )
    return response.choices[0].message


def _extract_tool_calls(message):
    calls = []
    for call in getattr(message, "tool_calls", None) or []:
        fn = getattr(call, "function", None)
        name = getattr(fn, "name", None)
        if not name:
            continue

        args, error = _parse_args(getattr(fn, "arguments", None))
        entry = {
            # Always assign an id so the assistant message and the tool result
            # the loop sends back refer to the same one.
            "id": getattr(call, "id", None) or f"call_{name}_{len(calls)}",
            "name": name,
            "args": args,
        }
        if error:
            entry["args_error"] = error
        calls.append(entry)
    return calls


def _reasoning_details(message):
    """Provider reasoning blocks that must be replayed unchanged with tool calls."""
    details = getattr(message, "reasoning_details", None)
    if not details:
        return None
    out = []
    for d in details:
        if hasattr(d, "model_dump"):
            d = d.model_dump(mode="json", by_alias=True, exclude_none=True)
        out.append(d)
    return out or None


def _build_assistant_message(message, tool_calls):
    """Assistant message to append to history for the next step."""
    if not tool_calls:
        return {"role": "assistant", "content": getattr(message, "content", None)}
    msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            _tool_call(c.get("id"), c["name"], c["args"]) for c in tool_calls
        ],
    }
    details = _reasoning_details(message)
    if details:
        msg["reasoning_details"] = details
    return msg


def _extract_usage(response):
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return {
        k: getattr(usage, k, None)
        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


# ------------------------------------------------------------ public API

def generate_step(
    contents,
    function_declarations,
    system_instruction=None,
    store=None,
):
    """Generate one agent step through the native OpenRouter SDK."""
    if store is None:
        raise RuntimeError(
            "OpenRouter requires a project store so its API key "
            "and model can be resolved."
        )

    cfg, model = _settings(store)
    pcfg = cfg.get("provider", {})

    messages = to_provider_contents(contents, store)
    if system_instruction:
        messages.insert(0, {"role": "system", "content": system_instruction})

    kwargs: dict[str, Any] = {
        "messages": messages,
        **_target(model, pcfg),
        **_routing(pcfg, needs_params=bool(function_declarations)),
    }
    if pcfg.get("temperature") is not None:
        kwargs["temperature"] = float(pcfg["temperature"])
    if function_declarations:
        kwargs["tools"] = to_openrouter_tools(function_declarations)
        kwargs["tool_choice"] = "auto"

    try:
        response = _send(store, pcfg, **kwargs)
    except Exception as exc:
        raise RuntimeError(
            f"OpenRouter request failed for model '{model}': {exc}"
        ) from exc

    message = _extract_message(response)
    tool_calls = _extract_tool_calls(message)
    text = getattr(message, "content", None)
    used_model = getattr(response, "model", None) or model  # actual routed model

    if not tool_calls and not (text or "").strip():
        reason = getattr(response.choices[0], "finish_reason", None)
        raise EmptyResponseError(
            f"The model returned an empty response (finish_reason={reason}, "
            f"model={used_model}). Try again or choose a different model."
        )

    return {
        "function_calls": tool_calls,
        "text": None if tool_calls else text,
        "model_content": _build_assistant_message(message, tool_calls),
        "usage": _extract_usage(response),
        "model": used_model,
    }


def build_function_response_content(name, result, call_id=None):
    """Build a tool result message for the next agent step."""
    return {
        "role": "tool",
        "tool_call_id": call_id or name,
        "content": json.dumps(result, ensure_ascii=False, default=str),
    }


def _clean_json_text(text):
    """Return text unchanged if it parses; else try to salvage fenced/wrapped JSON."""
    try:
        json.loads(text)
        return text
    except ValueError:
        pass
    stripped = _FENCE.sub("", text).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    for candidate in (stripped, stripped[start : end + 1] if 0 <= start < end else ""):
        try:
            json.loads(candidate)
            return candidate
        except ValueError:
            continue
    return text  # let the caller's own repair/retry logic handle it


def generate_json(store, prompt, model=None):
    """Generate JSON for cognition compilation through OpenRouter."""
    cfg, configured = _settings(store)
    pcfg = cfg.get("provider", {})
    model = model or configured
    request = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 8000,
        **_target(model, pcfg),
    }

    try:
        response = _send(
            store,
            pcfg,
            **request,
            **_routing(pcfg, needs_params=True),
            response_format={"type": "json_object"},
            # Server-side repair of malformed JSON (saves a repair round trip).
            plugins=[{"id": "response-healing"}],
        )
    except Exception:
        # Some routed models reject response_format/plugins; retry plain.
        try:
            response = _send(store, pcfg, **request, **_routing(pcfg, needs_params=False))
        except Exception as retry_exc:
            raise RuntimeError(
                f"OpenRouter JSON request failed for model '{model}': {retry_exc}"
            ) from retry_exc

    if not getattr(response, "choices", None):
        raise RuntimeError(f"OpenRouter returned no choices for model '{model}'.")

    choice = response.choices[0]
    text = getattr(choice.message, "content", None) or ""
    if not text.strip():
        raise RuntimeError(
            "OpenRouter returned an empty JSON response for model "
            f"'{model}' (finish_reason={getattr(choice, 'finish_reason', None)})."
        )
    return _clean_json_text(text)