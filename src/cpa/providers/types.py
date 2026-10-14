"""Shared shapes and errors for the provider layer.

Lowest layer: imports nothing from the rest of the package. Every provider is
spoken to through these types, so a stored conversation can be continued with
any provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


# ---------------------------------------------------------------- errors


class ProviderError(RuntimeError):
    """Base class for everything the provider layer raises."""


class ConfigError(ProviderError):
    """A provider is not set up (unknown id, missing key or endpoint)."""


class RequestError(ProviderError):
    """The provider call itself failed (network, auth, quota, bad model)."""


class EmptyResponseError(ProviderError):
    """The model returned neither text nor tool calls."""


# -------------------------------------------------------------- messages


@dataclass
class ToolSpec:
    """A tool the model may call. `parameters` is a JSON Schema object."""

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    error: str | None = None  # set when the model sent arguments that were not valid JSON


@dataclass
class Message:
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)  # assistant only
    tool_call_id: str | None = None  # tool only

    def __post_init__(self) -> None:
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("A tool message needs tool_call_id.")


@dataclass
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class ChatResult:
    provider: str
    model: str
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None
    finish_reason: str | None = None

    @property
    def message(self) -> Message:
        """The assistant message to append to the conversation history."""
        return Message("assistant", self.text, list(self.tool_calls))


# ---------------------------------------------------------------- config


@dataclass
class ProviderConfig:
    """Everything needed to talk to one provider. Resolved by Settings."""

    provider_id: str
    model: str
    api_key: str = field(repr=False)
    endpoint: str | None = None
    timeout: float = 120.0
    max_tokens: int = 8192  # only sent where the API requires it (Anthropic)
    temperature: float | None = None
