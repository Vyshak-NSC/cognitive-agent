"""Anthropic Messages API wire format.

Used by Anthropic and Azure Anthropic; they differ only in how the client is
built (see factory.py).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cpa.providers.temperature import call_with_temperature_fallback
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


def to_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
    ]


def to_messages(messages: Sequence[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Canonical messages -> (system prompt, Anthropic messages)."""
    system: list[str] = []
    out: list[dict[str, Any]] = []

    def push(role: str, blocks: list[dict[str, Any]]) -> None:
        # Anthropic wants alternating roles, and every result for one assistant
        # turn's tool calls must arrive together in a single user message.
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        if m.role == "system":
            if m.content:
                system.append(m.content)
        elif m.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            blocks.extend(
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in m.tool_calls
            )
            if blocks:
                push("assistant", blocks)
        elif m.role == "tool":
            push(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id,
                        "content": m.content or "",
                    }
                ],
            )
        elif m.content:
            push("user", [{"type": "text", "text": m.content}])
    return "\n\n".join(system), out


def complete(
    client: Any, cfg: ProviderConfig, messages: Sequence[Message], tools: Sequence[ToolSpec]
) -> ChatResult:
    system, anthropic_messages = to_messages(messages)
    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "messages": anthropic_messages,
        "max_tokens": cfg.max_tokens,  # required by this API
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = to_tools(tools)
        kwargs["tool_choice"] = {"type": "auto"}
    if cfg.temperature is not None:
        kwargs["temperature"] = cfg.temperature

    try:
        response = call_with_temperature_fallback(
            client.messages.create,
            kwargs,
        )
    except Exception as exc:
        raise RequestError(
            f"{cfg.provider_id} request failed for model '{cfg.model}': {exc}"
        ) from exc

    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in response.content or []:
        kind = getattr(block, "type", None)
        if kind == "text":
            texts.append(block.text)
        elif kind == "tool_use":
            calls.append(
                ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input if isinstance(block.input, dict) else {},
                )
            )
    text = "".join(texts)
    stop = getattr(response, "stop_reason", None)

    if not calls and not text.strip():
        raise EmptyResponseError(
            f"{cfg.provider_id} returned an empty response for model '{cfg.model}' "
            f"(stop_reason={stop})."
        )

    u = getattr(response, "usage", None)
    prompt, completion = getattr(u, "input_tokens", None), getattr(u, "output_tokens", None)
    return ChatResult(
        provider=cfg.provider_id,
        model=getattr(response, "model", None) or cfg.model,
        text=text or None,
        tool_calls=calls,
        usage=Usage(prompt, completion, (prompt or 0) + (completion or 0))
        if u is not None
        else None,
        finish_reason=stop,
    )
