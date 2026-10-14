"""Builds a Provider from a ProviderConfig.

The registry names the adapter module for each provider; this file imports them
all and checks that credentials exist before an adapter is created.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from cpa.providers.adapters import (
    anthropic,
    azure_anthropic,
    azure_openai,
    gemini,
    openai,
    openrouter,
)
from cpa.providers.registry import ProviderSpec, get_spec
from cpa.providers.types import (
    Adapter,
    ChatResult,
    ConfigError,
    Message,
    ProviderConfig,
    ToolSpec,
)

_ADAPTERS = {
    "gemini": gemini,
    "openrouter": openrouter,
    "openai": openai,
    "anthropic": anthropic,
    "azure_openai": azure_openai,
    "azure_anthropic": azure_anthropic,
}


@dataclass(frozen=True)
class Provider:
    spec: ProviderSpec
    config: ProviderConfig
    adapter: Adapter

    def chat(self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()) -> ChatResult:
        return self.adapter.chat(messages, tools)


def build_provider(cfg: ProviderConfig) -> Provider:
    spec = get_spec(cfg.provider_id)
    if not cfg.api_keys:
        raise ConfigError(f"No API key configured for {spec.label}. Set {spec.key_env}.")
    return Provider(spec=spec, config=cfg, adapter=_ADAPTERS[spec.adapter].create(cfg))
