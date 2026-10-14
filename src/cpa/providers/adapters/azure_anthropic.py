"""Azure Anthropic (Claude deployments on Azure AI Foundry)."""

from __future__ import annotations

from anthropic import AnthropicFoundry

from cpa.providers import wire_anthropic
from cpa.providers.adapters import azure
from cpa.providers.adapters.base import WireAdapter, cached_client
from cpa.providers.types import ProviderConfig


def create(cfg: ProviderConfig) -> WireAdapter:
    url = azure.base_url(cfg)
    key = ("azure_anthropic", cfg.api_key, url, cfg.timeout)
    client = cached_client(
        key, lambda: AnthropicFoundry(api_key=cfg.api_key, base_url=url, timeout=cfg.timeout)
    )
    return WireAdapter(client, cfg, wire_anthropic)
