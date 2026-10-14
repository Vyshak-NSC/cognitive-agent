from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from conftest import anthropic_response
from cpa.providers import (
    EmptyResponseError,
    Message,
    ProviderConfig,
    ToolCall,
    ToolSpec,
    wire_anthropic,
)

CFG = ProviderConfig(provider_id="anthropic", model="m", api_keys=("k",), max_tokens=777)


class FakeClient:
    def __init__(self, response):
        self.kwargs = None
        self._response = response
        self.messages = NS(create=self._create)

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return self._response


def test_system_is_separate_and_tool_results_merge_into_one_user_message():
    calls = [ToolCall("a", "f", {"x": 1}), ToolCall("b", "g", {})]
    system, out = wire_anthropic.to_messages(
        [
            Message("system", "s1"),
            Message("system", "s2"),
            Message("user", "go"),
            Message("assistant", "thinking", calls),
            Message("tool", "ra", tool_call_id="a"),
            Message("tool", "rb", tool_call_id="b"),
            Message("user", "thanks"),
        ]
    )
    assert system == "s1\n\ns2"
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert [b["type"] for b in out[1]["content"]] == ["text", "tool_use", "tool_use"]
    assert [b["type"] for b in out[2]["content"]] == ["tool_result", "tool_result", "text"]


def test_complete_always_sends_max_tokens_and_parses_blocks():
    blocks = [NS(type="text", text="hi "), NS(type="tool_use", id="t1", name="f", input={"k": "v"})]
    client = FakeClient(anthropic_response(blocks, "tool_use"))
    result = wire_anthropic.complete(client, CFG, [Message("user", "x")], [ToolSpec("f", "d")])
    assert client.kwargs["max_tokens"] == 777
    assert client.kwargs["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    assert result.text == "hi " and result.tool_calls[0].arguments == {"k": "v"}
    assert result.usage.total_tokens == 6 and result.finish_reason == "tool_use"


def test_empty_response_raises():
    client = FakeClient(anthropic_response([NS(type="text", text="")]))
    with pytest.raises(EmptyResponseError):
        wire_anthropic.complete(client, CFG, [Message("user", "x")], [])
