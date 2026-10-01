"""Pluggable, provider/tool/retrieval-neutral execution tracing.

The agent emits structured events here; frontends decide whether and how to
render them. No LLM/API call is made by the tracer.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import time

_CURRENT: ContextVar[object | None] = ContextVar("ragapp_execution_trace", default=None)

_SECRET_KEYS = {
    "api_key", "apikey", "authorization", "access_token", "refresh_token",
    "client_secret", "secret", "password", "token",
}


def _safe(value, max_chars=12000):
    """Make trace data JSON-safe and bounded without changing runtime data."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in _SECRET_KEYS or "api_key" in key_text.lower():
                out[key_text] = "[REDACTED]"
            else:
                out[key_text] = _safe(item, max_chars=max_chars)
        return out
    if isinstance(value, (list, tuple)):
        return [_safe(item, max_chars=max_chars) for item in value[:200]]
    if isinstance(value, set):
        return [_safe(item, max_chars=max_chars) for item in list(value)[:200]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > max_chars:
            return value[:max_chars] + f"… [truncated; {len(value)} chars]"
        return value
    return str(value)


def current_trace():
    return _CURRENT.get()


@contextmanager
def trace_scope(recorder):
    token = _CURRENT.set(recorder)
    try:
        yield recorder
    finally:
        _CURRENT.reset(token)


def trace_event(event_type, **data):
    recorder = current_trace()
    if recorder is None:
        return None
    return recorder.emit(event_type, **data)


class ExecutionTrace:
    """In-memory event recorder.

    This is intentionally independent of Streamlit. Pass None to AgentRunContext
    to disable tracing completely, or provide another recorder implementation
    exposing ``emit(event_type, **data)`` for a different frontend/backend.
    """

    def __init__(self, enabled=True, max_value_chars=12000):
        self.enabled = bool(enabled)
        self.max_value_chars = int(max_value_chars or 12000)
        self.started_at = time.monotonic()
        self.events = []
        self._sequence = 0

    def emit(self, event_type, **data):
        if not self.enabled:
            return None
        self._sequence += 1
        event = {
            "seq": self._sequence,
            "type": str(event_type),
            "time": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": round((time.monotonic() - self.started_at) * 1000, 2),
            "data": _safe(data, max_chars=self.max_value_chars),
        }
        self.events.append(event)
        return event

    def as_dict(self):
        return {"version": 1, "events": list(self.events)}

    def as_json(self):
        return json.dumps(self.as_dict(), ensure_ascii=False, default=str)

    @property
    def event_count(self):
        return len(self.events)
