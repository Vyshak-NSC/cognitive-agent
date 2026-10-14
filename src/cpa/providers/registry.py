"""The one place that knows which providers exist.

Data only. Provider ids, labels, environment-variable names, default models and
endpoints live here and nowhere else. Adding a provider means adding one entry.

Six providers, three client kinds:
  openai             OpenAI SDK; also used for Gemini and OpenRouter, which both
                     expose OpenAI-compatible endpoints, and for Azure OpenAI.
  anthropic          Anthropic SDK.
  anthropic_foundry  Anthropic SDK pointed at Azure AI Foundry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cpa.providers.types import ConfigError

ClientKind = Literal["openai", "anthropic", "anthropic_foundry"]

DEFAULT_PROVIDER = "gemini"


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    client: ClientKind
    default_model: str
    key_env: str
    model_env: str
    model_label: str = "Model"
    base_url: str | None = None  # fixed endpoint, or None for the SDK default
    endpoint_env: str | None = None  # user-supplied resource endpoint (Azure)
    endpoint_suffix: str = ""  # appended to that endpoint's origin


_AZURE_KEY = "AZURE_FOUNDRY_API_KEY"  # both Azure providers share one Foundry resource
_AZURE_ENDPOINT = "AZURE_FOUNDRY_ENDPOINT"

_SPECS = (
    ProviderSpec(
        id="gemini",
        label="Gemini",
        client="openai",
        default_model="gemini-3.5-flash-lite",
        key_env="GEMINI_API_KEY",
        model_env="GEMINI_MODEL",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    ),
    ProviderSpec(
        id="openrouter",
        label="OpenRouter",
        client="openai",
        default_model="liquid/lfm-2.5-2.6b:free",
        key_env="OPENROUTER_API_KEY",
        model_env="OPENROUTER_MODEL",
        base_url="https://openrouter.ai/api/v1",
    ),
    ProviderSpec(
        id="openai",
        label="OpenAI (GPT)",
        client="openai",
        default_model="gpt-5.6-luna",
        key_env="OPENAI_API_KEY",
        model_env="OPENAI_MODEL",
    ),
    ProviderSpec(
        id="anthropic",
        label="Anthropic (Claude)",
        client="anthropic",
        default_model="claude-sonnet-5-5",
        key_env="ANTHROPIC_API_KEY",
        model_env="ANTHROPIC_MODEL",
    ),
    ProviderSpec(
        id="azure_openai",
        label="Azure OpenAI (GPT on Azure Foundry)",
        client="openai",
        default_model="gpt-5.6-luna",
        key_env=_AZURE_KEY,
        model_env="AZURE_OPENAI_MODEL",
        model_label="Deployment name",
        endpoint_env=_AZURE_ENDPOINT,
        endpoint_suffix="/openai/v1",
    ),
    ProviderSpec(
        id="azure_anthropic",
        label="Azure Anthropic (Claude on Azure Foundry)",
        client="anthropic_foundry",
        default_model="claude-sonnet-5",
        key_env=_AZURE_KEY,
        model_env="AZURE_ANTHROPIC_MODEL",
        model_label="Deployment name",
        endpoint_env=_AZURE_ENDPOINT,
        endpoint_suffix="/anthropic",
    ),
)

SPECS: dict[str, ProviderSpec] = {s.id: s for s in _SPECS}


def get_spec(provider_id: str) -> ProviderSpec:
    try:
        return SPECS[provider_id]
    except KeyError:
        known = ", ".join(SPECS)
        raise ConfigError(f"Unknown provider '{provider_id}'. Known providers: {known}.") from None
