"""Pieces shared by the provider adapters, each written once.

- cached_client: one SDK client per credential set, so connection pools survive.
- WireAdapter: an adapter for providers whose whole behaviour is "build a client,
  then speak a shared wire format" (OpenAI, Azure OpenAI, Anthropic, Azure Anthropic).
- with_key_failover: try each configured key in order (Gemini, OpenRouter).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from cpa.providers.types import ChatResult, Message, ProviderConfig, RequestError, ToolSpec

T = TypeVar("T")

_CLIENTS: dict[tuple, Any] = {}
_LOCK = threading.Lock()


def cached_client(key: tuple, build: Callable[[], T]) -> T:
    with _LOCK:
        if key not in _CLIENTS:
            _CLIENTS[key] = build()
        return _CLIENTS[key]


@dataclass(frozen=True)
class WireAdapter:
    client: Any
    config: ProviderConfig
    wire: Any  # a wire module with complete(client, cfg, messages, tools)

    def chat(self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()) -> ChatResult:
        return self.wire.complete(self.client, self.config, messages, tools)


def with_key_failover(
    keys: Sequence[str],
    attempt: Callable[[str, bool], T],
    rotate_on: Callable[[Exception], bool],
) -> T:
    """Call `attempt(key, is_last)` with each key until one works.

    A failure that `rotate_on` accepts (quota, rate limit, bad key) moves on to the
    next key at once. Any other failure is raised immediately so one bad request
    does not burn through every key. If every key is rotated away, the error says so.
    """
    last_exc: Exception | None = None
    for i, key in enumerate(keys):
        is_last = i == len(keys) - 1
        try:
            return attempt(key, is_last)
        except Exception as exc:
            if not rotate_on(exc):
                raise
            last_exc = exc
            if not is_last:
                continue
            if len(keys) == 1:
                raise
    raise RequestError(
        f"All {len(keys)} configured API keys are exhausted or unavailable. Last error: {last_exc}"
    ) from last_exc
