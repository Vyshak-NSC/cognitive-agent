"""Backward-compatible LLM client facade.
Provider selection is handled by ragapp.llm.provider.  This module intentionally
does not instantiate Gemini at import time, so OpenRouter projects never fall
through to the Gemini SDK.
"""
from __future__ import annotations

def get_client(store):
    """Return the configured Provider instance for a project."""
    from ragapp.llm.provider import get_provider
    return get_provider(store)

# Legacy callers may import `client`; keep it None rather than constructing a
# Gemini client at module import time. New code should use get_client(store).
client = None
