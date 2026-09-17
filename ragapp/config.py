from __future__ import annotations
from pathlib import Path
import os
import yaml

_ENV_KEYS = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
_DEFAULT_MODELS = {
    "gemini": "gemini-3.5-flash-lite",
    "openrouter": "openrouter/free",
    "openai": "gpt-5.6-luna",
    "anthropic": "",
    "azure": "",
}

def project_config(store):
    p = store.root / ".system" / "config.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(
            "provider:\n  name: gemini\n  model: gemini-3.5-flash-lite\n  fallback: []\n  api_keys: {}\nlimits:\n  max_agent_steps: 8\n  transcript_turns: 40\n  transcript_chars: 80000\n  requests_per_minute: 15\nfeatures:\n  semantic_propagation: true\n",
            encoding="utf-8",
        )
    return p

def load_project_config(store):
    p = project_config(store)
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}

def save_project_config(store, data):
    p = project_config(store)
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return data

def get_api_keys(store, provider):
    base = _ENV_KEYS.get(provider)
    keys = []
    if base:
        for suffix in ("", "_2", "_3", "_4", "_5"):
            value = os.getenv(base + suffix)
            if value:
                keys.append(value)
    cfg = load_project_config(store).get("provider", {}).get("api_keys", {}).get(provider)
    if isinstance(cfg, list):
        keys.extend(str(k) for k in cfg if k)
    elif isinstance(cfg, str) and cfg:
        keys.append(cfg)
    seen = set()
    return [k for k in keys if not (k in seen or seen.add(k))]

def get_api_key(store, provider):
    keys = get_api_keys(store, provider)
    return keys[0] if keys else None

def resolve_model(store, env_model, default_model, provider=None):
    cfg = load_project_config(store).get("provider", {})
    configured_provider = cfg.get("name")
    target_provider = provider or configured_provider
    model = cfg.get("model")
    # A model explicitly stored for a different provider is unsafe to reuse.
    if configured_provider and provider and configured_provider != provider:
        model = None
    if model:
        return model
    if env_model and (not provider or configured_provider in (None, provider)):
        return env_model
    return default_model or _DEFAULT_MODELS.get(target_provider, "")
