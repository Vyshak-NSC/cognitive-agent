"""OpenAI chat-completions wire format.

Used by both the `openai` and `azure_openai` adapters; they differ only in how
the client is built.
"""
from __future__ import annotations

from typing import Any

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
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d.get("description", ""),
                "parameters": d.get("parameters", {"type": "object", "properties": {}}),
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
        args, error = parse_args(getattr(fn, "arguments", None))
        entry = {
            "id": getattr(call, "id", None) or f"call_{name}_{len(calls)}",
            "name": name,
            "args": args,
        }
        if error:
            entry["args_error"] = error
        calls.append(entry)
    return calls


def generate_step(client, model, pcfg, contents, function_declarations, system_instruction, label):
    messages = to_provider_contents(contents)
    if system_instruction:
        messages.insert(0, {"role": "system", "content": system_instruction})

    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if function_declarations:
        kwargs["tools"] = to_tools(function_declarations)
        kwargs["tool_choice"] = "auto"
    if pcfg.get("temperature") is not None:
        kwargs["temperature"] = float(pcfg["temperature"])

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        raise RuntimeError(f"{label} request failed for model '{model}': {exc}") from exc

    if not response.choices:
        raise RuntimeError(f"{label} returned no choices for model '{model}'.")
    choice = response.choices[0]
    message = choice.message
    calls = _extract_tool_calls(message)
    text = getattr(message, "content", None)

    if not calls and not (text or "").strip():
        raise EmptyResponseError(
            f"{label} returned an empty response for model '{model}' "
            f"(finish_reason={getattr(choice, 'finish_reason', None)})."
        )

    u = getattr(response, "usage", None)
    return {
        "function_calls": calls,
        "text": None if calls else text,
        "model_content": build_assistant_message(text, calls),
        "usage": usage(
            getattr(u, "prompt_tokens", None),
            getattr(u, "completion_tokens", None),
            getattr(u, "total_tokens", None),
        )
        if u is not None
        else None,
        "model": getattr(response, "model", None) or model,
    }


def generate_json(client, model, prompt, label):
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": JSON_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
    except Exception:
        # Some deployments reject response_format; retry as a plain request.
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": JSON_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            )
        except Exception as exc:
            raise RuntimeError(
                f"{label} JSON request failed for model '{model}': {exc}"
            ) from exc

    if not response.choices:
        raise RuntimeError(f"{label} returned no choices for JSON request '{model}'.")
    choice = response.choices[0]
    text = getattr(choice.message, "content", None) or ""
    if not text.strip():
        raise RuntimeError(
            f"{label} returned an empty JSON response for model '{model}' "
            f"(finish_reason={getattr(choice, 'finish_reason', None)})."
        )
    return clean_json_text(text)
