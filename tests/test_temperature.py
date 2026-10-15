from __future__ import annotations

import pytest

from cpa.providers.temperature import call_with_temperature_fallback


class ApiError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def fake_request(*errors: Exception):
    """A request function that raises the given errors in turn, then succeeds."""
    calls: list[dict] = []
    pending = list(errors)

    def request(**kwargs):
        calls.append(kwargs)
        if pending:
            raise pending.pop(0)
        return "ok"

    return request, calls


def test_retries_once_without_temperature_when_the_api_rejects_it():
    request, calls = fake_request(ApiError("`temperature` is deprecated for this model", 400))
    assert call_with_temperature_fallback(request, {"model": "m", "temperature": 0.5}) == "ok"
    assert calls == [{"model": "m", "temperature": 0.5}, {"model": "m"}]


def test_other_errors_are_not_retried():
    request, calls = fake_request(ApiError("rate limited, temperature too high", 429))
    with pytest.raises(ApiError):
        call_with_temperature_fallback(request, {"model": "m", "temperature": 0.5})
    assert len(calls) == 1


def test_a_400_about_something_else_is_not_retried():
    request, calls = fake_request(ApiError("max_tokens is too large", 400))
    with pytest.raises(ApiError):
        call_with_temperature_fallback(request, {"model": "m", "temperature": 0.5})
    assert len(calls) == 1


def test_no_retry_when_no_temperature_was_sent():
    request, calls = fake_request(ApiError("temperature is invalid", 400))
    with pytest.raises(ApiError):
        call_with_temperature_fallback(request, {"model": "m"})
    assert len(calls) == 1


def test_a_failure_on_the_retry_is_raised():
    request, calls = fake_request(ApiError("temperature unsupported", 400), ApiError("down", 500))
    with pytest.raises(ApiError, match="down"):
        call_with_temperature_fallback(request, {"model": "m", "temperature": 0.5})
    assert len(calls) == 2