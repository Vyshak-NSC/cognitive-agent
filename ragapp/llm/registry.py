"""Single source of truth for LLM providers and their defaults.

Data only (no imports from the rest of ragapp), so the UI, the adapters,
settings/config code and the agent loop can all import it without cycles.
Nothing else should hard-code a provider id, default model, key slot,
environment-variable name or limit default; add or change them here.
"""
from __future__ import annotations

DEFAULT_PROVIDER = "gemini"

# Anthropic-format requests must set max_tokens; overridable per project with
# ``provider.max_tokens``.
DEFAULT_MAX_TOKENS = 8192

# Per-project ``limits`` section. Read through limit(), never with a literal.
DEFAULT_LIMITS = {
    "max_agent_steps": 12,
    "transcript_turns": 40,
    "transcript_chars": 80000,
    "requests_per_minute": 15,
}

# id            -> what a project stores under ``provider.name``
# adapter       -> module in ragapp.llm.tool_calling that implements it
# key_slot      -> entry in the project's ``provider.api_keys``
# key_env       -> environment variable that overrides the stored key
#                  (None: resolved by ragapp.config)
# endpoint_env  -> environment variable for the endpoint; None means the
#                  provider has no endpoint setting
# The two Azure providers share one Foundry resource: same endpoint, same key
# slot; only the deployment differs.
PROVIDER_SPECS = {
    "gemini": {
        "label": "Gemini",
        "adapter": "gemini",
        "key_slot": "gemini",
        "key_env": None,
        "key_label": "Gemini API key",
        "endpoint_env": None,
        "model_label": "Model",
        "default_model": "gemini-3.5-flash-lite",
    },
    "openrouter": {
        "label": "OpenRouter",
        "adapter": "openrouter",
        "key_slot": "openrouter",
        "key_env": None,
        "key_label": "OpenRouter API key",
        "endpoint_env": None,
        "model_label": "Model",
        "default_model": "liquid/lfm-2.5-2.6b:free",
    },
    "openai": {
        "label": "OpenAI (GPT)",
        "adapter": "openai",
        "key_slot": "openai",
        "key_env": "OPENAI_API_KEY",
        "key_label": "OpenAI API key",
        "endpoint_env": None,
        "model_label": "Model",
        "default_model": "gpt-5.6-luna",
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "adapter": "anthropic",
        "key_slot": "anthropic",
        "key_env": "ANTHROPIC_API_KEY",
        "key_label": "Anthropic API key",
        "endpoint_env": None,
        "model_label": "Model",
        "default_model": "claude-sonnet-5-5",
    },
    "azure_openai": {
        "label": "Azure OpenAI (GPT on Azure Foundry)",
        "adapter": "azure_openai",
        "key_slot": "azure",
        "key_env": "AZURE_FOUNDRY_API_KEY",
        "key_label": "Azure Foundry API key (shared by both Azure providers)",
        "endpoint_env": "AZURE_FOUNDRY_ENDPOINT",
        "model_label": "Deployment name",
        "default_model": "gpt-5.6-luna",
    },
    "azure_anthropic": {
        "label": "Azure Anthropic (Claude on Azure Foundry)",
        "adapter": "azure_anthropic",
        "key_slot": "azure",
        "key_env": "AZURE_FOUNDRY_API_KEY",
        "key_label": "Azure Foundry API key (shared by both Azure providers)",
        "endpoint_env": "AZURE_FOUNDRY_ENDPOINT",
        "model_label": "Deployment name",
        "default_model": "claude-sonnet-5",
    },
}


def limit(cfg, name):
    """A configured limit, or its default from DEFAULT_LIMITS."""
    value = (cfg.get("limits") or {}).get(name)
    return DEFAULT_LIMITS[name] if value is None else value
