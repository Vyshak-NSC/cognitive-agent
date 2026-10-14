"""Provider layer: one way to talk to Gemini, OpenRouter, OpenAI, Anthropic,
Azure OpenAI and Azure Anthropic."""

from cpa.providers.factory import Provider, build_provider
from cpa.providers.registry import DEFAULT_PROVIDER, SPECS, ProviderSpec, get_spec
from cpa.providers.types import (
    ChatResult,
    ConfigError,
    EmptyResponseError,
    Message,
    ProviderConfig,
    ProviderError,
    RequestError,
    ToolCall,
    ToolSpec,
    Usage,
)

__all__ = [
    "ChatResult",
    "ConfigError",
    "DEFAULT_PROVIDER",
    "EmptyResponseError",
    "Message",
    "Provider",
    "ProviderConfig",
    "ProviderError",
    "ProviderSpec",
    "RequestError",
    "SPECS",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "build_provider",
    "get_spec",
]
