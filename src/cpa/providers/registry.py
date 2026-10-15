"""The one place that knows which providers exist.

Data only. Provider ids, labels, environment-variable names, default models,
endpoints and provider-specific options live here and nowhere else. Adding a
provider means adding one entry here and one adapter in `adapters/`.
"""

from __future__ import annotations

from dataclasses import dataclass

from cpa.providers.types import ConfigError

DEFAULT_PROVIDER = "gemini"


@dataclass(frozen=True)
class Option:
    """A provider-specific setting read from the environment."""

    name: str
    env: str
    default: str = ""


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    adapter: str  # module name in cpa.providers.adapters
    default_model: str
    key_env: str
    model_env: str
    model_label: str = "Model"
    multi_key: bool = False  # extra keys in KEY_2 .. KEY_9, tried in order on quota/auth errors
    endpoint_env: str | None = None  # user-supplied resource endpoint (Azure)
    endpoint_suffix: str = ""  # appended to that endpoint's origin
    options: tuple[Option, ...] = ()
    

_AZURE_KEY = "AZURE_FOUNDRY_API_KEY"  # both Azure providers share one Foundry resource
_AZURE_ENDPOINT = "AZURE_FOUNDRY_ENDPOINT"

_SPECS = (
    ProviderSpec(
        id="gemini",
        label="Gemini",
        adapter="gemini",
        default_model="gemini-3.5-flash-lite",
        key_env="GEMINI_API_KEY",
        model_env="GEMINI_MODEL",
        multi_key=True,
    ),
    ProviderSpec(
        id="openrouter",
        label="OpenRouter",
        adapter="openrouter",
        default_model="liquid/lfm-2.5-2.6b:free",
        key_env="OPENROUTER_API_KEY",
        model_env="OPENROUTER_MODEL",
        multi_key=True,
        options=(
            Option("fallback_models", "OPENROUTER_FALLBACK_MODELS"),  # comma-separated ids
            Option("require_parameters", "OPENROUTER_REQUIRE_PARAMETERS", "false"),
            Option("routing", "OPENROUTER_ROUTING"),  # JSON object of routing preferences
            Option("site_url", "OPENROUTER_SITE_URL"),
            Option("site_name", "OPENROUTER_SITE_NAME"),
            Option("retry_budget_seconds", "OPENROUTER_RETRY_BUDGET_SECONDS", "45"),
        ),
    ),
    ProviderSpec(
        id="openai",
        label="OpenAI (GPT)",
        adapter="openai",
        default_model="gpt-5.6-luna",
        key_env="OPENAI_API_KEY",
        model_env="OPENAI_MODEL",
    ),
    ProviderSpec(
        id="anthropic",
        label="Anthropic (Claude)",
        adapter="anthropic",
        default_model="claude-sonnet-5-5",
        key_env="ANTHROPIC_API_KEY",
        model_env="ANTHROPIC_MODEL",
    ),
    ProviderSpec(
        id="azure_openai",
        label="Azure OpenAI (GPT on Azure Foundry)",
        adapter="azure_openai",
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
        adapter="azure_anthropic",
        default_model="claude-sonnet-5",
        key_env=_AZURE_KEY,
        model_env="AZURE_ANTHROPIC_MODEL",
        model_label="Deployment name",
        endpoint_env=_AZURE_ENDPOINT,
        endpoint_suffix="/anthropic"
    ),
)

SPECS: dict[str, ProviderSpec] = {s.id: s for s in _SPECS}


def get_spec(provider_id: str) -> ProviderSpec:
    try:
        return SPECS[provider_id]
    except KeyError:
        known = ", ".join(SPECS)
        raise ConfigError(f"Unknown provider '{provider_id}'. Known providers: {known}.") from None


def option_env(provider_id: str, name: str) -> str:
    """The environment variable behind a provider option, for error messages."""
    return next(o.env for o in get_spec(provider_id).options if o.name == name)
