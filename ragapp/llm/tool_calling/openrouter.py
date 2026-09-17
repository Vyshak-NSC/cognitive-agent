"""OpenRouter/OpenAI-compatible chat-completions adapter."""
from __future__ import annotations
import json
import os
from typing import Any
from openai import OpenAI
from ragapp.config import get_api_key, load_project_config

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "openrouter/free"

# def _settings(store):
#     cfg = load_project_config(store)
#     provider = cfg.setdefault("provider", {})
#     model = (
#         os.getenv("OPENROUTER_MODEL")
#         or provider.get("model")
#         or DEFAULT_OPENROUTER_MODEL
#     )
#     base_url = provider.get("base_url") or OPENROUTER_BASE_URL
#     return cfg, model, base_url

def _settings(store):
    cfg = load_project_config(store)
    provider = cfg.setdefault("provider", {})

    configured_model = (
        os.getenv("OPENROUTER_MODEL")
        or provider.get("model")
    )

    # Do not send a model belonging to another provider to OpenRouter.
    if (
        not configured_model
        or configured_model.startswith("gemini")
        or (
            "/" not in configured_model
            and configured_model not in {"openrouter/free"}
        )
    ):
        model = DEFAULT_OPENROUTER_MODEL
    else:
        model = configured_model

    base_url = provider.get("base_url") or OPENROUTER_BASE_URL
    return cfg, model, base_url


def _client(store):
    key = get_api_key(store, "openrouter")
    if not key:
        raise RuntimeError(
            "No OpenRouter API key configured. "
            "Add it in Project Settings or set OPENROUTER_API_KEY."
        )
    _, _, base_url = _settings(store)
    provider = load_project_config(store).get("provider", {})
    headers = {}
    if provider.get("site_url"):
        headers["HTTP-Referer"] = provider["site_url"]
    if provider.get("site_name"):
        headers["X-Title"] = provider["site_name"]
    return OpenAI(
        api_key=key,
        base_url=base_url,
        default_headers=headers or None,
    )
def _canonical_tool_call(call):
    """Return the strict OpenAI/OpenRouter function-tool-call shape.
    Some OpenRouter providers return provider-specific fields/types. Those
    must never be replayed verbatim in the next request.
    """
    if not isinstance(call, dict):
        return None
    function = call.get("function") or {}
    if not isinstance(function, dict):
        function = {}
    name = function.get("name") or call.get("name")
    if not name:
        return None
    arguments = function.get("arguments", call.get("arguments", "{}"))
    if isinstance(arguments, (dict, list)):
        arguments = json.dumps(arguments, ensure_ascii=False)
    if not isinstance(arguments, str):
        arguments = str(arguments)
    return {
        "id": str(call.get("id") or f"call_{name}"),
        "type": "function",
        "function": {
            "name": str(name),
            "arguments": arguments,
        },
    }


def to_provider_contents(contents, store=None):
    """Convert internal messages into strict OpenAI/OpenRouter messages.
    In particular, sanitize replayed assistant tool calls so provider-specific
    ``type`` values cannot leak into a later request. This prevents provider
    validation errors such as:
      messages.*.tool_calls.*.type
    """
    out = []
    for item in contents:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "user")

        if role == "assistant" and item.get("tool_calls"):
            calls = [
                c for c in (
                    _canonical_tool_call(call)
                    for call in (item.get("tool_calls") or [])
                )
                if c is not None
            ]
            msg = {
                "role": "assistant",
                "content": item.get("content"),
            }
            if calls:
                msg["tool_calls"] = calls
                msg["content"] = None
            out.append(msg)
            continue

        if role == "tool":
            tool_call_id = item.get("tool_call_id") or item.get("id")
            if not tool_call_id:
                # An orphaned tool result cannot be represented safely.
                # Preserve its text as a user message rather than sending an
                # invalid tool message that the provider may reject.
                out.append({
                    "role": "user",
                    "content": item.get("content", ""),
                })
            else:
                out.append({
                    "role": "tool",
                    "tool_call_id": str(tool_call_id),
                    "content": item.get("content", ""),
                })
            continue

        if role == "system":
            out.append({
                "role": "system",
                "content": item.get("content", ""),
            })
            continue

        out.append({
            "role": "user" if role not in {"user"} else "user",
            "content": item.get("content", ""),
        })
    return out
def to_openrouter_tools(declarations):
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
def generate_step(
    contents,
    function_declarations,
    system_instruction=None,
    store=None,
):
    if store is None:
        raise RuntimeError(
            "OpenRouter requires a project store so its API key and "
            "model can be resolved."
        )
    _, model, _ = _settings(store)
    client = _client(store)
    messages = to_provider_contents(contents, store)
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
        kwargs["tools"] = to_openrouter_tools(function_declarations)
        kwargs["tool_choice"] = "auto"
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        raise RuntimeError(
            f"OpenRouter request failed for model '{model}': {exc}"
        ) from exc
    if not response.choices:
        raise RuntimeError(
            f"OpenRouter returned no choices for model '{model}'."
        )
    message = response.choices[0].message
    tool_calls = []
    for call in (getattr(message, "tool_calls", None) or []):
        raw_args = getattr(call.function, "arguments", "{}") or "{}"
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"OpenRouter returned invalid JSON arguments for "
                f"tool '{call.function.name}': {exc}"
            ) from exc
        tool_calls.append(
            {
                "id": getattr(call, "id", None),
                "name": call.function.name,
                "args": args if isinstance(args, dict) else {},
            }
        )
    # Reconstruct the replayable assistant message from the normalized
    # OpenRouter response. Do not reuse model_dump() because some upstream
    # providers add non-OpenAI discriminator values to tool calls.
    assistant_message = {
        "role": "assistant",
        "content": getattr(message, "content", None),
    }
    if tool_calls:
        assistant_message["content"] = None
        assistant_message["tool_calls"] = [
            {
                "id": call.get("id") or f"call_{call['name']}",
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(
                        call["args"],
                        ensure_ascii=False,
                    ),
                },
            }
            for call in tool_calls
        ]
    usage = None
    if getattr(response, "usage", None) is not None:
        usage_obj = response.usage
        usage = {
            "prompt_tokens": getattr(
                usage_obj,
                "prompt_tokens",
                None,
            ),
            "completion_tokens": getattr(
                usage_obj,
                "completion_tokens",
                None,
            ),
            "total_tokens": getattr(
                usage_obj,
                "total_tokens",
                None,
            ),
        }
    return {
        "function_calls": tool_calls,
        "text": (
            getattr(message, "content", None)
            if not tool_calls
            else None
        ),
        "model_content": assistant_message,
        "usage": usage,
        "model": model,
    }
def build_function_response_content(name, result, call_id=None):
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
    """Generate JSON for cognition compilation through OpenRouter."""
    client = _client(store)
    _, configured_model, _ = _settings(store)
    model = model or configured_model
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            response_format={"type": "json_object"},
        )
    except Exception:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            )
        except Exception as retry_exc:
            raise RuntimeError(
                f"OpenRouter JSON request failed for model "
                f"'{model}': {retry_exc}"
            ) from retry_exc
    if not response.choices:
        raise RuntimeError(
            f"OpenRouter returned no choices for model '{model}'."
        )
    text = response.choices[0].message.content or ""
    if not text.strip():
        raise RuntimeError(
            f"OpenRouter returned an empty JSON response for "
            f"model '{model}'."
        )
    return text