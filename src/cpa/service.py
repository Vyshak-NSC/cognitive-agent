"""The service: the single implementation behind both the API and the UI.

Both shells are thin: they turn their input (HTTP body, form field) into these
dataclasses, call `Service`, and show the result. No provider, settings or
translation logic exists outside this layer and below.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache

from cpa.providers import (
    ChatResult,
    Message,
    Provider,
    ProviderConfig,
    ToolSpec,
    build_provider,
)
from cpa.providers.registry import SPECS
from cpa.settings import Settings


@dataclass
class ProviderInfo:
    id: str
    label: str
    model: str
    model_label: str
    configured: bool
    missing: list[str]  # environment variables still to set
    default: bool = False  # True for the provider named by CPA_PROVIDER


@dataclass
class ChatRequest:
    messages: list[Message]
    provider: str | None = None  # None: the configured default provider
    model: str | None = None  # None: that provider's default model
    system: str | None = None
    temperature: float | None = None
    tools: list[ToolSpec] = field(default_factory=list)


class Service:
    def __init__(
        self,
        settings: Settings | None = None,
        build: Callable[[ProviderConfig], Provider] = build_provider,
    ) -> None:
        self.settings = settings or Settings.load()
        self._build = build

    def providers(self) -> list[ProviderInfo]:
        return [
            ProviderInfo(
                id=spec.id,
                label=spec.label,
                model=self.settings.default_model(spec.id),
                model_label=spec.model_label,
                configured=not (missing := self.settings.missing(spec.id)),
                missing=missing,
                default=spec.id == self.settings.default_provider
            )
            for spec in SPECS.values()
        ]

    def chat(self, request: ChatRequest) -> ChatResult:
        provider_id = request.provider or self.settings.default_provider
        config = self.settings.provider_config(
            provider_id, model=request.model, temperature=request.temperature
        )
        messages = list(request.messages)
        if request.system:
            messages.insert(0, Message("system", request.system))
        return self._build(config).chat(messages, request.tools)


@lru_cache(maxsize=1)
def get_service() -> Service:
    """The shared instance: the API and the UI both call this."""
    return Service()
