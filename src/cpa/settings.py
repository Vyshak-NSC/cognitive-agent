"""Settings: every environment variable the app reads, resolved in one place.

Provider key, endpoint and model variable names come from the provider registry;
the CPA_* variables below are the only others. Nothing else in the package reads
the environment.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from dotenv import load_dotenv

from cpa.providers.registry import DEFAULT_PROVIDER, get_spec
from cpa.providers.types import ProviderConfig


@dataclass(frozen=True)
class Settings:
    default_provider: str = DEFAULT_PROVIDER
    timeout: float = 120.0  # CPA_TIMEOUT_SECONDS
    max_tokens: int = 8192  # CPA_MAX_TOKENS
    api_host: str = "127.0.0.1"  # CPA_API_HOST
    api_port: int = 8000  # CPA_API_PORT
    env: Mapping[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Read settings from `env`, or from the process environment and a .env file."""
        if env is None:
            load_dotenv()
            env = os.environ
        env = dict(env)

        def text(name: str, default: str) -> str:
            return (env.get(name) or default).strip()

        settings = cls(
            default_provider=text("CPA_PROVIDER", DEFAULT_PROVIDER).lower(),
            timeout=float(text("CPA_TIMEOUT_SECONDS", "120")),
            max_tokens=int(text("CPA_MAX_TOKENS", "8192")),
            api_host=text("CPA_API_HOST", "127.0.0.1"),
            api_port=int(text("CPA_API_PORT", "8000")),
            env=env,
        )
        get_spec(settings.default_provider)  # fail early on a typo in CPA_PROVIDER
        return settings

    def _value(self, name: str | None) -> str:
        return (self.env.get(name) or "").strip() if name else ""

    def missing(self, provider_id: str) -> list[str]:
        """Names of required variables that are not set for this provider."""
        spec = get_spec(provider_id)
        needed = [spec.key_env] + ([spec.endpoint_env] if spec.endpoint_env else [])
        return [name for name in needed if not self._value(name)]

    def default_model(self, provider_id: str) -> str:
        spec = get_spec(provider_id)
        return self._value(spec.model_env) or spec.default_model

    def provider_config(
        self, provider_id: str, *, model: str | None = None, temperature: float | None = None
    ) -> ProviderConfig:
        spec = get_spec(provider_id)
        return ProviderConfig(
            provider_id=provider_id,
            model=(model or "").strip() or self.default_model(provider_id),
            api_key=self._value(spec.key_env),
            endpoint=self._value(spec.endpoint_env) or None,
            timeout=self.timeout,
            max_tokens=self.max_tokens,
            temperature=temperature,
        )
