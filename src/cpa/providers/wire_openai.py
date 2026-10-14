"""OpenAI chat-completions wire format.

Shared by every adapter that speaks this dialect: OpenAI, Azure OpenAI and
OpenRouter. They differ in client, auth and extras, not in what a message,
tool or response looks like, so the translation is written once here.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from cpa.providers.types import (
    ChatResult,
    EmptyResponseError,
    Message,
    ProviderConfig,
    RequestError,
    ToolCall,
    ToolSpec,
    Usage,
)
from cpa.providers.util import parse_arguments, to_plain


def to_messages(
    messages: Sequence[Message], extras_keys: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """Canonical messages -> wire messages.

    `extras_keys` names Message.extras entries to copy onto assistant tool-call
    turns (OpenRouter replays its reasoning blocks this way). Other providers
    pass nothing, so data from another provider is never sent to them.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant":
            if m.tool_calls:
                turn: dict[str, Any] = {
                    "role": "assistant",
                    "content": m.content,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.name,
                                "arguments": json.dumps(c.arguments, ensure_ascii=False),
                            },
                        }
                        for c in m.tool_calls
                    ],
                }
                turn.update({k: m.extras[k] for k in extras_keys if m.extras.get(k)})
                out.append(turn)
            elif m.content:  # empty assistant turns are dropped
                out.append({"role": "assistant", "content": m.content})
        elif m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""})
        elif m.content:
            out.append({"role": m.role, "content": m.content})
    return out


def to_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]


def _tool_calls(message: Any) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for raw in getattr(message, "tool_calls", None) or []:
        fn = getattr(raw, "function", None)
        name = getattr(fn, "name", None)
        if not name:
            continue
        arguments, error = parse_arguments(getattr(fn, "arguments", None))
        calls.append(
            ToolCall(
                id=getattr(raw, "id", None) or f"call_{name}_{len(calls)}",
                name=name,
                arguments=arguments,
                error=error,
            )
        )
    return calls


def parse_response(
    response: Any, cfg: ProviderConfig, extras_keys: Sequence[str] = ()
) -> ChatResult:
    """A chat-completions response (any SDK's object or a plain namespace) -> ChatResult."""
    if not getattr(response, "choices", None):
        detail = getattr(response, "error", None)
        raise RequestError(
            f"{cfg.provider_id} returned no choices for model '{cfg.model}'."
            + (f" Provider error: {to_plain(detail)}" if detail else "")
        )
    choice = response.choices[0]
    text = getattr(choice.message, "content", None)
    calls = _tool_calls(choice.message)
    finish = getattr(choice, "finish_reason", None)
    finish = None if finish is None else str(finish)

    if not calls and not (text or "").strip():
        raise EmptyResponseError(
            f"{cfg.provider_id} returned an empty response for model "
            f"'{getattr(response, 'model', None) or cfg.model}' (finish_reason={finish})."
        )

    extras = {}
    for key in extras_keys:
        value = getattr(choice.message, key, None)
        if value:
            extras[key] = to_plain(value)

    u = getattr(response, "usage", None)
    return ChatResult(
        provider=cfg.provider_id,
        model=getattr(response, "model", None) or cfg.model,
        text=text or None,
        tool_calls=calls,
        usage=Usage(
            getattr(u, "prompt_tokens", None),
            getattr(u, "completion_tokens", None),
            getattr(u, "total_tokens", None),
        )
        if u is not None
        else None,
        finish_reason=finish,
        extras=extras,
    )


def complete(
    client: Any, cfg: ProviderConfig, messages: Sequence[Message], tools: Sequence[ToolSpec]
) -> ChatResult:
    """One request through an OpenAI-SDK-style client (OpenAI, Azure OpenAI)."""
    kwargs: dict[str, Any] = {"model": cfg.model, "messages": to_messages(messages)}
    if tools:
        kwargs["tools"] = to_tools(tools)
        kwargs["tool_choice"] = "auto"
    if cfg.temperature is not None:
        kwargs["temperature"] = cfg.temperature

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        raise RequestError(
            f"{cfg.provider_id} request failed for model '{cfg.model}': {exc}"
        ) from exc
    return parse_response(response, cfg)
