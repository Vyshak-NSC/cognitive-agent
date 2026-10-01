"""Anthropic Messages API wire format.

Used by both the `anthropic` and `azure_anthropic` adapters; they differ only
in how the client is built.
"""
from __future__ import annotations

from typing import Any

from ragapp.llm.registry import DEFAULT_MAX_TOKENS
from ragapp.llm.tool_calling.common import (
    EmptyResponseError,
    build_assistant_message,
    clean_json_text,
    parse_args,
    to_provider_contents,
    usage,
)

JSON_SYSTEM_PROMPT = "Return only valid JSON. Do not use markdown fences."


def to_tools(declarations):
    return [
        {
            "name": d["name"],
            "description": d.get("description", ""),
            "input_schema": d.get("parameters", {"type": "object", "properties": {}}),
        }
        for d in declarations
    ]


def to_messages(messages):
    """Canonical messages -> (system prompt, Anthropic messages)."""
    system, out = [], []

    def push(role, blocks):
        # Anthropic wants alternating roles, and all results for one assistant
        # turn's tool calls must arrive together in a single user message.
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role == "system":
            if content:
                system.append(str(content))
        elif role == "assistant":
            blocks = []
            if content:
                blocks.append({"type": "text", "text": str(content)})
            for c in m.get("tool_calls") or []:
                fn = c["function"]
                args, _ = parse_args(fn.get("arguments"))
                blocks.append(
                    {"type": "tool_use", "id": c["id"], "name": fn["name"], "input": args}
                )
            if blocks:
                push("assistant", blocks)
        elif role == "tool":
            push(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": m["tool_call_id"],
                        "content": str(content),
                    }
                ],
            )
        elif content:
            push("user", [{"type": "text", "text": str(content)}])
    return "\n\n".join(system), out


def _max_tokens(pcfg):
    return int(pcfg.get("max_tokens") or DEFAULT_MAX_TOKENS)


def generate_step(client, model, pcfg, contents, function_declarations, system_instruction, label):
    messages = to_provider_contents(contents)
    if system_instruction:
        messages.insert(0, {"role": "system", "content": system_instruction})
    system, anthropic_messages = to_messages(messages)

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": anthropic_messages,
        "max_tokens": _max_tokens(pcfg),
    }
    if system:
        kwargs["system"] = system
    if function_declarations:
        kwargs["tools"] = to_tools(function_declarations)
        kwargs["tool_choice"] = {"type": "auto"}
    if pcfg.get("temperature") is not None:
        kwargs["temperature"] = float(pcfg["temperature"])

    try:
        response = client.messages.create(**kwargs)
    except Exception as exc:
        raise RuntimeError(f"{label} request failed for model '{model}': {exc}") from exc

    texts, calls = [], []
    for block in response.content or []:
        kind = getattr(block, "type", None)
        if kind == "text":
            texts.append(block.text)
        elif kind == "tool_use":
            calls.append(
                {
                    "id": block.id,
                    "name": block.name,
                    "args": block.input if isinstance(block.input, dict) else {},
                }
            )
    text = "".join(texts) or None

    if not calls and not (text or "").strip():
        raise EmptyResponseError(
            f"{label} returned an empty response for model '{model}' "
            f"(stop_reason={getattr(response, 'stop_reason', None)})."
        )

    u = getattr(response, "usage", None)
    return {
        "function_calls": calls,
        "text": None if calls else text,
        "model_content": build_assistant_message(text, calls),
        "usage": usage(
            getattr(u, "input_tokens", None),
            getattr(u, "output_tokens", None),
        )
        if u is not None
        else None,
        "model": getattr(response, "model", None) or model,
    }


def generate_json(client, model, prompt, pcfg, label):
    try:
        response = client.messages.create(
            model=model,
            system=JSON_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=_max_tokens(pcfg),
        )
    except Exception as exc:
        raise RuntimeError(f"{label} JSON request failed for model '{model}': {exc}") from exc

    text = "".join(
        b.text for b in (response.content or []) if getattr(b, "type", None) == "text"
    )
    if not text.strip():
        raise RuntimeError(
            f"{label} returned an empty JSON response for model '{model}' "
            f"(stop_reason={getattr(response, 'stop_reason', None)})."
        )
    return clean_json_text(text)
