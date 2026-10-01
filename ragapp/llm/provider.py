from __future__ import annotations

import importlib
import threading
import time

from ragapp.config import get_api_key, load_project_config
from ragapp.llm.registry import DEFAULT_PROVIDER, PROVIDER_SPECS, limit
from ragapp.agent.trace import trace_event


class ProviderError(RuntimeError):
    pass


# Provider objects are rebuilt on every call (see get_provider), so the
# throttle state must live at module level or it is reset each time.
_LAST_CALL = {}
_THROTTLE_LOCK = threading.Lock()


def configured_name(store):
    """The project's provider id, validated against PROVIDER_SPECS."""
    if store is None:
        raise ProviderError("A project store is required for provider resolution.")
    name = str(
        load_project_config(store).get("provider", {}).get("name") or DEFAULT_PROVIDER
    ).strip().lower()
    if name not in PROVIDER_SPECS:
        raise ProviderError(
            f"Unknown provider '{name}'. Choose one of: {', '.join(PROVIDER_SPECS)}."
        )
    return name


def adapter_module(name):
    return importlib.import_module(
        f"ragapp.llm.tool_calling.{PROVIDER_SPECS[name]['adapter']}"
    )


def adapter_for(store):
    return adapter_module(configured_name(store))


class Provider:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self.spec = PROVIDER_SPECS[name]
        self.cfg = load_project_config(store)
        self.adapter = adapter_module(name)
        self.model = self.adapter.model_name(store)

    def throttle(self):
        rpm = float(limit(self.cfg, "requests_per_minute"))
        gap = 60.0 / max(rpm, 1.0)
        key = (self.name, str(getattr(self.store, "root", id(self.store))))
        with _THROTTLE_LOCK:
            now = time.monotonic()
            slot = max(now, _LAST_CALL.get(key, 0.0) + gap)
            _LAST_CALL[key] = slot  # reserve the slot so concurrent calls queue up
        if slot > now:
            time.sleep(slot - now)

    def key(self):
        return get_api_key(self.store, self.spec["key_slot"])

    def generate(self, contents, function_declarations=None, system_instruction=None):
        self.throttle()
        request_id = f"{self.name}:{time.monotonic_ns()}"
        trace_event(
            "api_request",
            request_id=request_id,
            provider=self.name,
            adapter=self.spec.get("adapter"),
            model=self.model,
            message_count=len(contents or []),
            messages=[
                {
                    "role": item.get("role") if isinstance(item, dict) else None,
                    "chars": len(str(item.get("content", ""))) if isinstance(item, dict) else len(str(item)),
                }
                for item in (contents or [])
            ],
            system_chars=len(system_instruction or ""),
            tool_count=len(function_declarations or []),
            tools=[
                d.get("name") for d in (function_declarations or [])
                if isinstance(d, dict) and d.get("name")
            ],
        )
        started = time.monotonic()
        try:
            result = self.adapter.generate_step(
                contents, function_declarations, system_instruction, store=self.store
            )
        except Exception as exc:
            trace_event(
                "api_error",
                request_id=request_id,
                provider=self.name,
                adapter=self.spec.get("adapter"),
                model=self.model,
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            raise
        trace_event(
            "api_response",
            request_id=request_id,
            provider=self.name,
            adapter=self.spec.get("adapter"),
            model=result.get("model") or self.model if isinstance(result, dict) else self.model,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
            usage=result.get("usage") if isinstance(result, dict) else None,
            function_calls=[
                {"id": c.get("id"), "name": c.get("name"), "args": c.get("args")}
                for c in (result.get("function_calls") or [])
                if isinstance(c, dict)
            ] if isinstance(result, dict) else [],
            response_chars=len(str(result.get("text") or "")) if isinstance(result, dict) else 0,
        )
        return result

    def generate_json(self, prompt, model=None):
        self.throttle()
        request_id = f"{self.name}:json:{time.monotonic_ns()}"
        selected_model = model or self.model
        trace_event(
            "api_request",
            request_id=request_id,
            provider=self.name,
            adapter=self.spec.get("adapter"),
            operation="generate_json",
            model=selected_model,
            prompt_chars=len(str(prompt or "")),
        )
        started = time.monotonic()
        try:
            result = self.adapter.generate_json(self.store, prompt, model=model)
        except Exception as exc:
            trace_event(
                "api_error",
                request_id=request_id,
                provider=self.name,
                adapter=self.spec.get("adapter"),
                operation="generate_json",
                model=selected_model,
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            raise
        trace_event(
            "api_response",
            request_id=request_id,
            provider=self.name,
            adapter=self.spec.get("adapter"),
            operation="generate_json",
            model=selected_model,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
            response_chars=len(str(result or "")),
        )
        return result


def get_provider(store):
    return Provider(store, configured_name(store))


def generate_step(contents, function_declarations, system_instruction=None, store=None):
    """Provider-independent generation entry point."""
    return get_provider(store).generate(
        contents,
        function_declarations=function_declarations,
        system_instruction=system_instruction,
    )


def generate_json(store, prompt, model=None):
    """Provider-independent JSON generation entry point."""
    return get_provider(store).generate_json(prompt, model=model)
