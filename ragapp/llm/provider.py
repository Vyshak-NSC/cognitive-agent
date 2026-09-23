from __future__ import annotations
import threading
import time
from ragapp.config import get_api_key, load_project_config, resolve_model
from ragapp.settings import CHAT_MODEL, DEFAULT_CHAT_MODEL

class ProviderError(RuntimeError):
    pass

# Provider objects are rebuilt on every call (see get_provider), so the
# throttle state must live at module level or it is reset each time.
_LAST_CALL = {}
_THROTTLE_LOCK = threading.Lock()

class Provider:
    name = "base"

    def __init__(self, store):
        self.store = store
        self.cfg = load_project_config(store)
        self._last = 0.0

    def throttle(self):
        rpm = float(self.cfg.get("limits", {}).get("requests_per_minute", 15))
        gap = 60.0 / max(rpm, 1.0)
        key = (self.name, str(getattr(self.store, "root", id(self.store))))
        with _THROTTLE_LOCK:
            now = time.monotonic()
            slot = max(now, _LAST_CALL.get(key, 0.0) + gap)
            _LAST_CALL[key] = slot  # reserve the slot so concurrent calls queue up
        if slot > now:
            time.sleep(slot - now)

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
        _, self.model = _settings(store)

    def generate(self, *args, **kwargs):
        from ragapp.llm.tool_calling.openrouter import generate_step
        self.throttle()
        return generate_step(*args, **kwargs, store=self.store)

    def generate_json(self, prompt, model=None):
        from ragapp.llm.tool_calling.openrouter import generate_json
        self.throttle()
        return generate_json(self.store, prompt, model=model or self.model)


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, store):
        super().__init__(store)
        from ragapp.config import resolve_model
        self.model = resolve_model(
            store,
            CHAT_MODEL,
            DEFAULT_CHAT_MODEL,
            provider=self.name,
        )

    def generate(
        self,
        contents,
        system_instruction="",
        function_declarations=None,
        model=None,
    ):
        from ragapp.llm.tool_calling.openai import generate_step
        return generate_step(
            self.store,
            contents,
            system_instruction=system_instruction,
            function_declarations=function_declarations,
            model=model or self.model,
        )

    def generate_json(self, prompt, model=None):
        from ragapp.llm.tool_calling.openai import generate_json
        return generate_json(
            self.store,
            prompt,
            model=model or self.model,
        )

class AzureOpenAIProvider(Provider):
    name = "azure"

    def __init__(self, store):
        super().__init__(store)
        from ragapp.llm.tool_calling.azure_openai import _settings
        _, _, _, _, self.model = _settings(store)

    def generate(
        self,
        contents,
        system_instruction="",
        function_declarations=None,
        model=None,
    ):
        from ragapp.llm.tool_calling.azure_openai import generate_step
        self.throttle()
        return generate_step(
            self.store,
            contents,
            system_instruction=system_instruction,
            function_declarations=function_declarations,
            model=model or self.model,
        )

    def generate_json(self, prompt, model=None):
        from ragapp.llm.tool_calling.azure_openai import generate_json
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
    if name == "openai":
        return OpenAIProvider(store)
    if name in {"azure", "azure_openai"}:
        return AzureOpenAIProvider(store)
    if name in {"anthropic"}:
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
        system_instruction=system_instruction,
        function_declarations=function_declarations,
    )


def generate_json(store, prompt, model=None):
    """Provider-independent JSON generation entry point."""
    if store is None:
        raise ProviderError("A project store is required for provider resolution.")
    return get_provider(store).generate_json(prompt, model=model)