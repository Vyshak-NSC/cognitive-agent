"""Gemini through the native google-genai SDK.

Gemini has its own message format (Content and Part objects, function calls and
responses as parts, the system prompt in the request config), so this adapter
translates the shared messages to it and back. It also keeps Gemini's
thought signatures, which Gemini 3 requires when a tool-call turn is replayed.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from google import genai
from google.genai import types

from cpa.providers.adapters.base import cached_client, with_key_failover
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

EXTRAS_KEY = "gemini_parts"  # the model's own parts, replayed unchanged

# A key that cannot serve the request: try the next one straight away.
_KEY_PROBLEMS = (
    "quota",
    "resource_exhausted",
    "rate limit",
    "ratelimit",
    "too many requests",
    "429",
    "unauthenticated",
    "permission denied",
    "api key",
)

_EMPTY_HINTS = {
    "SAFETY": " (blocked by a safety filter)",
    "PROHIBITED_CONTENT": " (blocked as prohibited content)",
    "RECITATION": " (blocked for recitation)",
    "MAX_TOKENS": " (hit the output-token limit)",
}


def _key_problem(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(term in text for term in _KEY_PROBLEMS)


def _name(value: Any) -> str:
    """'STOP' for both the enum FinishReason.STOP and the string 'STOP'."""
    return str(getattr(value, "name", value) or "")


# --------------------------------------------------------------- request


def _result_payload(text: str | None) -> dict[str, Any]:
    """Tool output as a function response: JSON stays structured, anything else is text."""
    try:
        value = json.loads(text or "")
    except ValueError:
        value = text or ""
    return {"result": value}


def to_contents(messages: Sequence[Message]) -> tuple[str, list[types.Content]]:
    """Shared messages -> (system prompt, Gemini contents)."""
    system: list[str] = []
    contents: list[types.Content] = []
    names: dict[str, str] = {}  # tool_call_id -> function name (Gemini answers by name)

    def push(role: str, parts: list[types.Part]) -> None:
        if contents and contents[-1].role == role:
            contents[-1].parts.extend(parts)  # all results of one turn go in one content
        else:
            contents.append(types.Content(role=role, parts=list(parts)))

    for m in messages:
        if m.role == "system":
            if m.content:
                system.append(m.content)
        elif m.role == "assistant":
            for call in m.tool_calls:
                names[call.id] = call.name
            if m.extras.get(EXTRAS_KEY):
                parts = [types.Part.model_validate(p) for p in m.extras[EXTRAS_KEY]]
            else:
                parts = [types.Part.from_text(text=m.content)] if m.content else []
                parts += [
                    types.Part(function_call=types.FunctionCall(name=c.name, args=c.arguments))
                    for c in m.tool_calls
                ]
            if parts:
                push("model", parts)
        elif m.role == "tool":
            name = names.get(m.tool_call_id or "", m.tool_call_id or "tool")
            part = types.Part.from_function_response(name=name, response=_result_payload(m.content))
            push("user", [part])
        elif m.content:
            push("user", [types.Part.from_text(text=m.content)])
    return "\n\n".join(system), contents


def to_tool(tools: Sequence[ToolSpec]) -> types.Tool:
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters_json_schema=t.parameters
            )
            for t in tools
        ]
    )


# -------------------------------------------------------------- response


def parse_response(response: Any, cfg: ProviderConfig) -> ChatResult:
    if not response.candidates:
        feedback = getattr(response, "prompt_feedback", None)
        reason = _name(getattr(feedback, "block_reason", None))
        raise EmptyResponseError(
            f"gemini returned no candidates for model '{cfg.model}'"
            + (f" (prompt blocked: {reason})." if reason else ".")
        )
    candidate = response.candidates[0]
    finish = _name(getattr(candidate, "finish_reason", None))
    parts = list(candidate.content.parts or []) if candidate.content is not None else []
    if not parts:
        hint = _EMPTY_HINTS.get(finish, f" (finish_reason={finish})" if finish else "")
        raise EmptyResponseError(
            f"gemini returned an empty response for model '{cfg.model}'{hint}. "
            "Try rephrasing the request or asking again."
        )

    text = "".join(p.text for p in parts if p.text and not p.thought)
    calls = [
        ToolCall(
            id=f"call_{p.function_call.name}_{i}",
            name=p.function_call.name,
            arguments=dict(p.function_call.args or {}),
        )
        for i, p in enumerate(q for q in parts if q.function_call)
    ]
    u = getattr(response, "usage_metadata", None)
    return ChatResult(
        provider=cfg.provider_id,
        model=getattr(response, "model_version", None) or cfg.model,
        text=text or None,
        tool_calls=calls,
        usage=Usage(
            getattr(u, "prompt_token_count", None),
            getattr(u, "candidates_token_count", None),
            getattr(u, "total_token_count", None),
        )
        if u is not None
        else None,
        finish_reason=finish or None,
        # Signatures ride on the model's own parts; keep them for the next request.
        extras={EXTRAS_KEY: [p.model_dump(mode="json", exclude_none=True) for p in parts]}
        if calls
        else {},
    )


# --------------------------------------------------------------- adapter


class GeminiAdapter:
    def __init__(self, cfg: ProviderConfig) -> None:
        self.config = cfg

    def chat(self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()) -> ChatResult:
        cfg = self.config
        system, contents = to_contents(messages)
        gen_config = types.GenerateContentConfig(
            system_instruction=system or None,
            tools=[to_tool(tools)] if tools else None,
            temperature=cfg.temperature,
            # A hung request used to leave the UI spinning forever; bound every call.
            http_options=types.HttpOptions(timeout=int(cfg.timeout * 1000)),
        )

        def attempt(key: str, _is_last: bool) -> Any:
            client = cached_client(("gemini", key), lambda: genai.Client(api_key=key))
            return client.models.generate_content(
                model=cfg.model, contents=contents, config=gen_config
            )

        try:
            response = with_key_failover(cfg.api_keys, attempt, _key_problem)
        except RequestError:
            raise
        except Exception as exc:
            raise RequestError(f"gemini request failed for model '{cfg.model}': {exc}") from exc
        return parse_response(response, cfg)


def create(cfg: ProviderConfig) -> GeminiAdapter:
    return GeminiAdapter(cfg)
