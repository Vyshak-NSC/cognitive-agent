"""OpenAI (GPT) through the official OpenAI SDK."""

from __future__ import annotations

from openai import OpenAI

from cpa.providers import wire_openai
from cpa.providers.adapters.base import WireAdapter, cached_client
from cpa.providers.types import ProviderConfig


def create(cfg: ProviderConfig, base_url: str | None = None) -> WireAdapter:
    """`base_url` is set by Azure OpenAI, which uses the same SDK and wire format."""
    key = ("openai", cfg.api_key, base_url, cfg.timeout)
    client = cached_client(
        key, lambda: OpenAI(api_key=cfg.api_key, base_url=base_url, timeout=cfg.timeout)
    )
    return WireAdapter(client, cfg, wire_openai)
