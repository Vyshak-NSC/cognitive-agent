"""Azure AI Foundry connection details shared by the two Azure adapters.

Both Azure providers use one Foundry resource and one key; only the deployment and
the API path differ (set per provider in the registry).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from cpa.providers.registry import get_spec
from cpa.providers.types import ConfigError, ProviderConfig


def base_url(cfg: ProviderConfig) -> str:
    spec = get_spec(cfg.provider_id)
    if not cfg.endpoint or not cfg.endpoint.strip():
        raise ConfigError(f"No endpoint configured for {spec.label}. Set {spec.endpoint_env}.")
    text = cfg.endpoint.strip()
    parts = urlsplit(text if "://" in text else f"https://{text}")
    # Keep only scheme and host: a pasted /openai/... or /anthropic path is dropped.
    return f"{parts.scheme}://{parts.netloc}{spec.endpoint_suffix}"
