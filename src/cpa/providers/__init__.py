"""Provider layer: one way to talk to Gemini, OpenRouter, OpenAI, Anthropic,
Azure OpenAI and Azure Anthropic."""

from cpa.providers.factory import Provider, build_provider
from cpa.providers.registry import DEFAULT_PROVIDER, SPECS, Option, ProviderSpec, get_spec
from cpa.providers.types import (
    Adapter,
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
    "DEFAULT_PROVIDER",
    "SPECS",
    "Adapter",
    "ChatResult",
    "ConfigError",
    "EmptyResponseError",
    "Message",
    "Option",
    "Provider",
    "ProviderConfig",
    "ProviderError",
    "ProviderSpec",
    "RequestError",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "build_provider",
    "get_spec",
]
