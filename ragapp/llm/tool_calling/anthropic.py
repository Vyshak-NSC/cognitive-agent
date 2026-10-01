"""Anthropic (Claude) through the Anthropic API."""
from __future__ import annotations

from anthropic import Anthropic

from ragapp.config import load_project_config
from ragapp.llm.tool_calling import anthropic_wire
from ragapp.llm.tool_calling.common import (  # noqa: F401  (adapter API)
    EmptyResponseError,
    build_function_response_content,
    cached_client,
    configured_model,
    credential,
    to_provider_contents,
)

PROVIDER = "anthropic"


def model_name(store):
    return configured_model(store, PROVIDER)


def _connection(store):
    if store is None:
        raise RuntimeError(f"{PROVIDER} requires a project store.")
    key = credential(store, PROVIDER)
    if not key:
        raise RuntimeError(f"No API key configured for {PROVIDER}. Add it in Project Settings or set its environment variable.")
    pcfg = load_project_config(store).get("provider", {})
    client = cached_client((PROVIDER, key), lambda: Anthropic(api_key=key))
    return pcfg, model_name(store), client


def generate_step(contents, function_declarations, system_instruction=None, store=None):
    pcfg, model, client = _connection(store)
    return anthropic_wire.generate_step(
        client, model, pcfg, contents, function_declarations, system_instruction, PROVIDER
    )


def generate_json(store, prompt, model=None):
    pcfg, configured, client = _connection(store)
    return anthropic_wire.generate_json(client, model or configured, prompt, pcfg, PROVIDER)
