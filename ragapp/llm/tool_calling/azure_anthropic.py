"""Azure Anthropic (Claude deployments on Azure AI Foundry)."""
from __future__ import annotations

from anthropic import AnthropicFoundry

from ragapp.llm.tool_calling import anthropic_wire, azure_foundry
from ragapp.llm.tool_calling.common import (  # noqa: F401  (adapter API)
    EmptyResponseError,
    build_function_response_content,
    cached_client,
    configured_model,
    to_provider_contents,
)

PROVIDER = "azure_anthropic"


def model_name(store):
    return configured_model(store, PROVIDER)


def _connection(store):
    pcfg, model, key, origin = azure_foundry.connection(store, PROVIDER)
    base_url = f"{origin}/anthropic"
    client = cached_client(
        (PROVIDER, base_url, key),
        lambda: AnthropicFoundry(api_key=key, base_url=base_url),
    )
    return pcfg, model, client


def generate_step(contents, function_declarations, system_instruction=None, store=None):
    pcfg, model, client = _connection(store)
    return anthropic_wire.generate_step(
        client, model, pcfg, contents, function_declarations, system_instruction, PROVIDER
    )


def generate_json(store, prompt, model=None):
    pcfg, configured, client = _connection(store)
    return anthropic_wire.generate_json(client, model or configured, prompt, pcfg, PROVIDER)
