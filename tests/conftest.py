from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cpa.providers import ChatResult, Message, Usage
from cpa.settings import Settings


@pytest.fixture
def settings() -> Settings:
    """Every provider configured, from a fake environment (never the real one)."""
    return Settings.load(
        {
            "GEMINI_API_KEY": "g-key",
            "OPENROUTER_API_KEY": "or-key",
            "OPENAI_API_KEY": "oa-key",
            "ANTHROPIC_API_KEY": "an-key",
            "AZURE_FOUNDRY_API_KEY": "az-key",
            "AZURE_FOUNDRY_ENDPOINT": "my-res.services.ai.azure.com",
        }
    )


class FakeProvider:
    """Stands in for a built Provider; records what it was asked."""

    def __init__(self, config):
        self.config = config
        self.seen: list[Message] = []

    def chat(self, messages, tools=()):
        self.seen = list(messages)
        return ChatResult(
            provider=self.config.provider_id,
            model=self.config.model,
            text="hello",
            usage=Usage(3, 2, 5),
        )


def openai_response(text="hi", tool_calls=None, finish="stop"):
    message = NS(content=text, tool_calls=tool_calls)
    return NS(
        choices=[NS(message=message, finish_reason=finish)],
        usage=NS(prompt_tokens=4, completion_tokens=2, total_tokens=6),
        model="served-model",
    )


def anthropic_response(blocks, stop="end_turn"):
    return NS(
        content=blocks,
        stop_reason=stop,
        usage=NS(input_tokens=4, output_tokens=2),
        model="served-claude",
    )
