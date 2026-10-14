"""OpenRouter through its native SDK.

OpenRouter routes to many backends, so this adapter carries the behaviour that
comes with that: several keys tried in order, retries that honour Retry-After,
a server-side chain of fallback models, routing preferences, attribution
headers, tolerant parsing of sloppy backend responses, and replay of the
reasoning blocks some models require alongside tool calls.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

from openrouter import OpenRouter
from openrouter.utils import BackoffStrategy, RetryConfig

from cpa.providers import wire_openai
from cpa.providers.adapters.base import cached_client, with_key_failover
from cpa.providers.registry import option_env
from cpa.providers.types import (
    ChatResult,
    ConfigError,
    Message,
    ProviderConfig,
    RequestError,
    ToolSpec,
)

REPLAYED = ("reasoning_details",)  # Message.extras entries sent back with tool calls

_RETRY_STATUS = ["500", "502", "503", "504"]  # 429 is added for the last key only
_ROTATE_STATUS = {401, 402, 429}  # bad key, no credits, rate limited: try the next key


# --------------------------------------------------------------- options


def _flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _routing(options: dict[str, str], needs_parameters: bool) -> dict[str, Any]:
    """OpenRouter provider-routing preferences, or {} when there are none."""
    prefs: dict[str, Any] = {}
    if needs_parameters and _flag(options.get("require_parameters", "")):
        prefs["require_parameters"] = True  # only route to backends that support tools
    raw = options.get("routing", "").strip()
    if raw:
        try:
            extra = json.loads(raw)
        except ValueError as exc:
            raise ConfigError(
                f"{option_env('openrouter', 'routing')} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(extra, dict):
            raise ConfigError(f"{option_env('openrouter', 'routing')} must be a JSON object.")
        prefs.update(extra)
    return {"provider": prefs} if prefs else {}


def _target(model: str, options: dict[str, str]) -> dict[str, Any]:
    """`model`, or `models=[...]` (a server-side fallback chain) when fallbacks are set."""
    fallbacks = [m.strip() for m in options.get("fallback_models", "").split(",") if m.strip()]
    if fallbacks:
        return {"models": [model, *[m for m in fallbacks if m != model]]}
    return {"model": model}


# ---------------------------------------------------------------- client


def _client(key: str, cfg: ProviderConfig, retry_429: bool) -> OpenRouter:
    """One cached client per key and policy, so the connection pool lives across calls."""
    o = cfg.options
    budget_s = float(o.get("retry_budget_seconds") or 45)
    cache_key = (
        "openrouter",
        key,
        o.get("site_url"),
        o.get("site_name"),
        cfg.timeout,
        budget_s,
        retry_429,
    )

    def build() -> OpenRouter:
        kwargs: dict[str, Any] = {
            "api_key": key,
            "timeout_ms": int(cfg.timeout * 1000),
            # SDK backoff; it also honours Retry-After headers.
            "retry_config": RetryConfig(
                "backoff",
                BackoffStrategy(500, 8000, 2.0, int(budget_s * 1000)),
                True,
                _RETRY_STATUS + (["429"] if retry_429 else []),
            ),
        }
        if o.get("site_url"):
            kwargs["http_referer"] = o["site_url"]
        if o.get("site_name"):
            kwargs["x_open_router_title"] = o["site_name"]
        return OpenRouter(**kwargs)

    return cached_client(cache_key, build)


def _lenient(exc: Exception) -> Any | None:
    """Salvage a 200 response the SDK's strict schema rejected.

    Routed backends (free ones especially) sometimes omit fields the SDK marks as
    required, such as an empty tool-call id. The payload is still usable.
    """
    if type(exc).__name__ != "ResponseValidationError":
        return None
    try:
        data = json.loads(exc.body)
    except (TypeError, ValueError, AttributeError):
        return None
    return _namespace(data) if isinstance(data, dict) else None


def _namespace(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _namespace(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_namespace(v) for v in value]
    return value


# --------------------------------------------------------------- adapter


class OpenRouterAdapter:
    def __init__(self, cfg: ProviderConfig) -> None:
        self.config = cfg

    def _send(self, **kwargs: Any) -> Any:
        cfg = self.config

        def attempt(key: str, is_last: bool) -> Any:
            try:
                # Only the last key waits out a 429 with backoff; the others rotate at once.
                return _client(key, cfg, retry_429=is_last).chat.send(**kwargs)
            except Exception as exc:
                salvaged = _lenient(exc)
                if salvaged is not None:
                    return salvaged
                raise

        return with_key_failover(
            cfg.api_keys, attempt, lambda exc: getattr(exc, "status_code", None) in _ROTATE_STATUS
        )

    def chat(self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()) -> ChatResult:
        cfg = self.config
        if "/" not in cfg.model:
            raise ConfigError(
                f"'{cfg.model}' is not an OpenRouter model id. They look like vendor/model, "
                "for example google/gemini-2.5-flash."
            )
        kwargs: dict[str, Any] = {
            "messages": wire_openai.to_messages(messages, REPLAYED),
            **_target(cfg.model, cfg.options),
            **_routing(cfg.options, needs_parameters=bool(tools)),
        }
        if cfg.temperature is not None:
            kwargs["temperature"] = cfg.temperature
        if tools:
            kwargs["tools"] = wire_openai.to_tools(tools)
            kwargs["tool_choice"] = "auto"

        try:
            response = self._send(**kwargs)
        except (ConfigError, RequestError):
            raise
        except Exception as exc:
            raise RequestError(f"openrouter request failed for model '{cfg.model}': {exc}") from exc
        return wire_openai.parse_response(response, cfg, REPLAYED)


def create(cfg: ProviderConfig) -> OpenRouterAdapter:
    return OpenRouterAdapter(cfg)
