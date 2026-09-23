from __future__ import annotations
import json
import os
import re
from typing import Any
from openai import AzureOpenAI, OpenAI
from ragapp.config import get_api_key, load_project_config, resolve_model
from ragapp.settings import CHAT_MODEL, DEFAULT_CHAT_MODEL
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)
_CLIENTS = {}
class EmptyResponseError(RuntimeError):
    pass
def _settings(store):
    cfg = load_project_config(store)
    provider = cfg.get("provider", {})
    endpoint = (
        os.getenv("AZURE_OPENAI_ENDPOINT")
        or os.getenv("AZURE_FOUNDRY_ENDPOINT")
        or provider.get("azure_endpoint")
        or provider.get("endpoint")
        or ""
    ).strip().rstrip("/")
    api_version = (
        os.getenv("AZURE_OPENAI_API_VERSION")
        or provider.get("api_version")
        or "2024-10-21"
    )
    deployment = (
        os.getenv("AZURE_OPENAI_DEPLOYMENT")
        or os.getenv("AZURE_FOUNDRY_MODEL")
        or provider.get("deployment")
        or provider.get("model")
        or resolve_model(
            store,
            CHAT_MODEL,
            DEFAULT_CHAT_MODEL,
            provider="azure",
        )
    )
    key = get_api_key(store, "azure")
    if not key:
        raise RuntimeError(
            "No Azure API key configured. "
            "Set AZURE_OPENAI_API_KEY or add the Azure key in Project Settings."
        )
    if not endpoint:
        raise RuntimeError(
            "No Azure endpoint configured. "
            "Set AZURE_OPENAI_ENDPOINT or add 'endpoint' to the project provider config."
        )
    return cfg, endpoint, api_version, key, deployment
def _is_foundry_endpoint(endpoint):
    return (
        "services.ai.azure.com" in endpoint
        or "inference.ai.azure.com" in endpoint
        or endpoint.endswith("/openai/v1")
        or "/openai/v1/" in endpoint
    )
def _client(store):
    cfg, endpoint, api_version, key, deployment = _settings(store)
    cache_key = (
        endpoint,
        api_version,
        key,
        _is_foundry_endpoint(endpoint),
    )
    client = _CLIENTS.get(cache_key)
    if client is not None:
        return client
    if _is_foundry_endpoint(endpoint):
        base_url = endpoint
        if not base_url.endswith("/openai/v1"):
            base_url = base_url.rstrip("/") + "/openai/v1"
        client = OpenAI(
            api_key=key,
            base_url=base_url,
        )
    else:
        client = AzureOpenAI(
            api_key=key,
            azure_endpoint=endpoint,
            api_version=api_version,
        )
    _CLIENTS[cache_key] = client
    return client
def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
def _args_json(args):
    if args is None:
        return "{}"
    if isinstance(args, str):
        return args
    if isinstance(args, (dict, list)):
        return json.dumps(args, ensure_ascii=False)
    return str(args)
def _tool_call(call_id, name, arguments):
    return {
        "id": str(call_id or f"call_{name}"),
        "type": "function",
        "function": {
            "name": str(name),
            "arguments": _args_json(arguments),
        },
    }
def _canonical_tool_call(call):
    if call is None:
        return None
    fn = _get(call, "function") or {}
    name = _get(fn, "name") or _get(call, "name")
    if not name:
        return None
    args = _get(
        fn,
        "arguments",
        _get(call, "arguments", "{}"),
    )
    return _tool_call(
        _get(call, "id"),
        name,
        args,
    )
def _parse_args(raw):
    if isinstance(raw, dict):
        return raw, None
    if raw is None:
        return {}, None
    text = raw if isinstance(raw, str) else str(raw)
    for candidate in (
        text,
        _FENCE.sub("", text),
    ):
        try:
            value = json.loads(candidate)
            return (
                value if isinstance(value, dict) else {},
                None,
            )
        except (TypeError, ValueError):
            pass
    return {}, f"Tool arguments were not valid JSON: {text[:200]!r}"
def to_provider_contents(contents, store=None):
    out = []
    for item in contents:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "user")
        content = item.get("content", "")
        if role in ("assistant", "model"):
            calls = [
                c
                for c in (
                    _canonical_tool_call(x)
                    for x in (item.get("tool_calls") or [])
                )
                if c is not None
            ]
            if calls:
                out.append(
                    {
                        "role": "assistant",
                        "content": content or None,
                        "tool_calls": calls,
                    }
                )
            elif content:
                out.append(
                    {
                        "role": "assistant",
                        "content": content,
                    }
                )
        elif role == "tool":
            call_id = item.get("tool_call_id") or item.get("id")
            if call_id:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call_id),
                        "content": str(content),
                    }
                )
            elif content:
                out.append(
                    {
                        "role": "user",
                        "content": str(content),
                    }
                )
        elif role == "system":
            if content:
                out.append(
                    {
                        "role": "system",
                        "content": content,
                    }
                )
        elif content:
            out.append(
                {
                    "role": "user",
                    "content": content,
                }
            )
    return out
def to_azure_tools(declarations):
    return [
        {
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d.get("description", ""),
                "parameters": d.get(
                    "parameters",
                    {
                        "type": "object",
                        "properties": {},
                    },
                ),
            },
        }
        for d in declarations
    ]
def _extract_tool_calls(message):
    calls = []
    for call in getattr(message, "tool_calls", None) or []:
        fn = getattr(call, "function", None)
        name = getattr(fn, "name", None)
        if not name:
            continue
        args, error = _parse_args(
            getattr(fn, "arguments", None)
        )
        entry = {
            "id": getattr(call, "id", None)
            or f"call_{name}_{len(calls)}",
            "name": name,
            "args": args,
        }
        if error:
            entry["args_error"] = error
        calls.append(entry)
    return calls
def _build_assistant_message(message, tool_calls):
    if not tool_calls:
        return {
            "role": "assistant",
            "content": getattr(message, "content", None),
        }
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            _tool_call(
                call.get("id"),
                call["name"],
                call["args"],
            )
            for call in tool_calls
        ],
    }
def _clean_json_text(text):
    if not text:
        return text
    try:
        json.loads(text)
        return text
    except ValueError:
        pass
    stripped = _FENCE.sub("", text).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        candidate = stripped[start:end + 1]
        try:
            json.loads(candidate)
            return candidate
        except ValueError:
            pass
    return text
def generate_step(
    store,
    contents,
    system_instruction=None,
    function_declarations=None,
    model=None,
):
    if store is None:
        raise RuntimeError(
            "Azure OpenAI requires a project store."
        )
    cfg, endpoint, api_version, key, model = _settings(store)
    messages = to_provider_contents(
        contents,
        store,
    )
    if system_instruction:
        messages.insert(
            0,
            {
                "role": "system",
                "content": system_instruction,
            },
        )
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if function_declarations:
        kwargs["tools"] = to_azure_tools(
            function_declarations
        )
        kwargs["tool_choice"] = "auto"
    provider_cfg = cfg.get("provider", {})
    if provider_cfg.get("temperature") is not None:
        kwargs["temperature"] = float(
            provider_cfg["temperature"]
        )
    try:
        response = _client(store).chat.completions.create(
            **kwargs
        )
    except Exception as exc:
        raise RuntimeError(
            f"Azure request failed for model/deployment "
            f"'{model}': {exc}"
        ) from exc
    if not response.choices:
        raise RuntimeError(
            f"Azure returned no choices for '{model}'."
        )
    message = response.choices[0].message
    tool_calls = _extract_tool_calls(message)
    text = getattr(message, "content", None)
    if not tool_calls and not (text or "").strip():
        raise EmptyResponseError(
            f"Azure returned an empty response for '{model}'."
        )
    usage = getattr(response, "usage", None)
    usage_data = None
    if usage is not None:
        usage_data = {
            "prompt_tokens": getattr(
                usage,
                "prompt_tokens",
                None,
            ),
            "completion_tokens": getattr(
                usage,
                "completion_tokens",
                None,
            ),
            "total_tokens": getattr(
                usage,
                "total_tokens",
                None,
            ),
        }
    return {
        "function_calls": tool_calls,
        "text": None if tool_calls else text,
        "model_content": _build_assistant_message(
            message,
            tool_calls,
        ),
        "usage": usage_data,
        "model": getattr(
            response,
            "model",
            None,
        ) or model,
    }
def build_function_response_content(
    name,
    result,
    call_id=None,
):
    return {
        "role": "tool",
        "tool_call_id": call_id or name,
        "content": json.dumps(
            result,
            ensure_ascii=False,
            default=str,
        ),
    }
def generate_json(store, prompt, model=None):
    if store is None:
        raise RuntimeError(
            "Azure OpenAI requires a project store."
        )
    _, _, _, _, configured_model = _settings(store)
    model = model or configured_model
    try:
        response = _client(store).chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Return only valid JSON. "
                        "Do not use markdown fences."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            response_format={
                "type": "json_object"
            },
        )
    except Exception:
        response = _client(store).chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        )
    if not response.choices:
        raise RuntimeError(
            f"Azure returned no choices for JSON request '{model}'."
        )
    text = (
        getattr(
            response.choices[0].message,
            "content",
            None,
        )
        or ""
    )
    if not text.strip():
        raise RuntimeError(
            f"Azure returned an empty JSON response for '{model}'."
        )
    return _clean_json_text(text)