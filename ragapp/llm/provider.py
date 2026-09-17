from __future__ import annotations
import time
from ragapp.config import get_api_key, load_project_config, resolve_model
from ragapp.settings import CHAT_MODEL, DEFAULT_CHAT_MODEL

class ProviderError(RuntimeError):
    pass

class Provider:
    name = "base"

    def __init__(self, store):
        self.store = store
        self.cfg = load_project_config(store)
        self._last = 0.0

    def throttle(self):
        rpm = float(self.cfg.get("limits", {}).get("requests_per_minute", 15))
        gap = 60.0 / max(rpm, 1.0)
        wait = gap - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def key(self):
        return get_api_key(self.store, self.name)

    def generate(self, *args, **kwargs):
        raise NotImplementedError

    def generate_json(self, prompt, model=None):
        raise NotImplementedError


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, store):
        super().__init__(store)
        # self.model = resolve_model(store, CHAT_MODEL, DEFAULT_CHAT_MODEL)
        self.model = resolve_model(
            store,
            CHAT_MODEL,
            DEFAULT_CHAT_MODEL,
            provider=self.name,
        )

    def generate(self, *args, **kwargs):
        from ragapp.llm.tool_calling.gemini import generate_step
        self.throttle()
        return generate_step(*args, **kwargs, store=self.store)

    def generate_json(self, prompt, model=None):
        from ragapp.llm.tool_calling.gemini import generate_json
        self.throttle()
        return generate_json(self.store, prompt, model=model or self.model)


class OpenRouterProvider(Provider):
    name = "openrouter"

    def __init__(self, store):
        super().__init__(store)
        from ragapp.llm.tool_calling.openrouter import _settings
        _, self.model, _ = _settings(store)

    def generate(self, *args, **kwargs):
        from ragapp.llm.tool_calling.openrouter import generate_step
        self.throttle()
        return generate_step(*args, **kwargs, store=self.store)

    def generate_json(self, prompt, model=None):
        from ragapp.llm.tool_calling.openrouter import generate_json
        self.throttle()
        return generate_json(self.store, prompt, model=model or self.model)


def get_provider(store):
    name = (
        load_project_config(store)
        .get("provider", {})
        .get("name", "openrouter")
        .lower()
        .strip()
    )
    if name == "gemini":
        return GeminiProvider(store)
    if name == "openrouter":
        return OpenRouterProvider(store)
    if name in {"openai", "anthropic", "azure"}:
        raise ProviderError(
            f"{name} is configured but its adapter is not enabled in this installation."
        )
    raise ProviderError(f"Unknown provider: {name}")


def generate_step(contents, function_declarations, system_instruction=None, store=None):
    """Provider-independent generation entry point."""
    if store is None:
        raise ProviderError("A project store is required for provider resolution.")
    return get_provider(store).generate(
        contents,
        function_declarations,
        system_instruction=system_instruction,
    )


def generate_json(store, prompt, model=None):
    """Provider-independent JSON generation entry point."""
    if store is None:
        raise ProviderError("A project store is required for provider resolution.")
    return get_provider(store).generate_json(prompt, model=model)
