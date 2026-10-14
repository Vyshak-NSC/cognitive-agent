"""Anthropic (Claude) through the official Anthropic SDK."""

from __future__ import annotations

from anthropic import Anthropic

from cpa.providers import wire_anthropic
from cpa.providers.adapters.base import WireAdapter, cached_client
from cpa.providers.types import ProviderConfig


def create(cfg: ProviderConfig) -> WireAdapter:
    key = ("anthropic", cfg.api_key, cfg.timeout)
    client = cached_client(key, lambda: Anthropic(api_key=cfg.api_key, timeout=cfg.timeout))
    return WireAdapter(client, cfg, wire_anthropic)
