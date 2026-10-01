"""Connection settings shared by `azure_openai` and `azure_anthropic`.

Both providers talk to the same Azure AI Foundry resource with the same key;
only the model deployment (and therefore the API dialect) differs. Endpoint
and key variable names, key slot and default deployment are in
ragapp.llm.registry.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from ragapp.config import load_project_config
from ragapp.llm.registry import PROVIDER_SPECS
from ragapp.llm.tool_calling.common import configured_model, credential


def connection(store, provider_id):
    """(provider_cfg, deployment, api_key, resource_origin)."""
    if store is None:
        raise RuntimeError(f"{provider_id} requires a project store.")
    spec = PROVIDER_SPECS[provider_id]
    pcfg = load_project_config(store).get("provider", {})

    key = credential(store, provider_id)
    if not key:
        raise RuntimeError(
            f"No Azure Foundry API key configured for {provider_id}. "
            f"Add it in Project Settings or set {spec['key_env']}."
        )

    endpoint = (os.getenv(spec["endpoint_env"]) or pcfg.get("endpoint") or "").strip()
    if not endpoint:
        raise RuntimeError(
            f"No Azure Foundry endpoint configured for {provider_id}. "
            f"Add it in Project Settings or set {spec['endpoint_env']}."
        )
    parts = urlsplit(endpoint if "://" in endpoint else f"https://{endpoint}")
    origin = f"{parts.scheme}://{parts.netloc}"  # any pasted /openai/... or /anthropic path is dropped
    return pcfg, configured_model(store, provider_id), key, origin
