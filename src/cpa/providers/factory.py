"""Builds a Provider from a ProviderConfig.

Clients are built here and only here. A Provider is a registry spec, a resolved
config and an SDK client; `chat()` hands them to the matching wire module.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from anthropic import Anthropic, AnthropicFoundry
from openai import OpenAI

from cpa.providers import wire_anthropic, wire_openai
from cpa.providers.registry import ProviderSpec, get_spec
from cpa.providers.types import ChatResult, ConfigError, Message, ProviderConfig, ToolSpec

_WIRES = {
    "openai": wire_openai,
    "anthropic": wire_anthropic,
    "anthropic_foundry": wire_anthropic,
}

_CLIENTS: dict[tuple, Any] = {}
_LOCK = threading.Lock()


def resolve_base_url(spec: ProviderSpec, endpoint: str | None) -> str | None:
    """Fixed URL from the registry, or the user's Azure resource plus the API path."""
    if not spec.endpoint_env:
        return spec.base_url
    if not endpoint or not endpoint.strip():
        raise ConfigError(f"No endpoint configured for {spec.label}. Set {spec.endpoint_env}.")
    text = endpoint.strip()
    parts = urlsplit(text if "://" in text else f"https://{text}")
    # Keep only scheme and host: a pasted /openai/... or /anthropic path is dropped.
    return f"{parts.scheme}://{parts.netloc}{spec.endpoint_suffix}"


def _client(spec: ProviderSpec, cfg: ProviderConfig) -> Any:
    if not cfg.api_key.strip():
        raise ConfigError(f"No API key configured for {spec.label}. Set {spec.key_env}.")
    url = resolve_base_url(spec, cfg.endpoint)
    key = (spec.id, cfg.api_key, url, cfg.timeout)
    with _LOCK:  # one client per key, so the connection pool is reused between calls
        if key not in _CLIENTS:
            if spec.client == "openai":
                _CLIENTS[key] = OpenAI(api_key=cfg.api_key, base_url=url, timeout=cfg.timeout)
            elif spec.client == "anthropic":
                _CLIENTS[key] = Anthropic(api_key=cfg.api_key, timeout=cfg.timeout)
            else:
                _CLIENTS[key] = AnthropicFoundry(
                    api_key=cfg.api_key, base_url=url, timeout=cfg.timeout
                )
        return _CLIENTS[key]


@dataclass(frozen=True)
class Provider:
    spec: ProviderSpec
    config: ProviderConfig
    client: Any

    def chat(self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()) -> ChatResult:
        return _WIRES[self.spec.client].complete(self.client, self.config, messages, tools)


def build_provider(cfg: ProviderConfig) -> Provider:
    spec = get_spec(cfg.provider_id)
    return Provider(spec=spec, config=cfg, client=_client(spec, cfg))
