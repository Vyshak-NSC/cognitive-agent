"""Retry requests without temperature when the API explicitly rejects it."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


def temperature_unsupported(exc: Exception) -> bool:
    """True for a 400 error that names temperature (the wording differs per API and model)."""
    return getattr(exc, "status_code", None) == 400 and "temperature" in str(exc).lower()


def call_with_temperature_fallback(
    request: Callable[..., T],
    kwargs: dict[str, Any],
) -> T:
    """Retry once without temperature if the API rejects that parameter."""
    try:
        return request(**kwargs)
    except Exception as exc:
        if "temperature" not in kwargs or not temperature_unsupported(exc):
            raise

        retry_kwargs = {key: value for key, value in kwargs.items() if key != "temperature"}
        return request(**retry_kwargs)
