from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from conftest import openai_response
from cpa.providers import (
    EmptyResponseError,
    Message,
    ProviderConfig,
    RequestError,
    ToolCall,
    ToolSpec,
    wire_openai,
)

CFG = ProviderConfig(provider_id="openai", model="m", api_keys=("k",))


class FakeClient:
    def __init__(self, response=None, error=None):
        self.kwargs = None
        self._response, self._error = response, error
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        if self._error:
            raise self._error
        return self._response


def test_messages_translate_with_tool_round_trip():
    call = ToolCall("c1", "lookup", {"q": "x"})
    out = wire_openai.to_messages(
        [
            Message("system", "be brief"),
            Message("user", "hi"),
            Message("assistant", None, [call]),
            Message("tool", "42", tool_call_id="c1"),
            Message("assistant", ""),  # empty turn is dropped
        ]
    )
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    assert out[2]["tool_calls"][0]["function"] == {
        "name": "lookup",
        "arguments": json.dumps({"q": "x"}),
    }
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": "42"}


def test_complete_text_and_usage():
    client = FakeClient(openai_response("hello"))
    result = wire_openai.complete(client, CFG, [Message("user", "hi")], [])
    assert result.text == "hello" and result.model == "served-model"
    assert result.usage.total_tokens == 6 and result.finish_reason == "stop"
    assert "tools" not in client.kwargs and "temperature" not in client.kwargs


def test_complete_sends_tools_and_temperature_when_set():
    client = FakeClient(openai_response("ok"))
    cfg = ProviderConfig(provider_id="openai", model="m", api_keys=("k",), temperature=0.2)
    wire_openai.complete(client, cfg, [Message("user", "hi")], [ToolSpec("t", "d")])
    assert client.kwargs["tools"][0]["function"]["name"] == "t"
    assert client.kwargs["tool_choice"] == "auto" and client.kwargs["temperature"] == 0.2


def test_complete_parses_tool_calls_and_bad_arguments():
    good = NS(id="a", function=NS(name="f", arguments='{"x": 1}'))
    bad = NS(id="b", function=NS(name="g", arguments="not json"))
    client = FakeClient(openai_response(None, [good, bad], "tool_calls"))
    result = wire_openai.complete(client, CFG, [Message("user", "go")], [ToolSpec("f")])
    assert result.tool_calls[0].arguments == {"x": 1} and result.tool_calls[0].error is None
    assert result.tool_calls[1].arguments == {} and "not valid JSON" in result.tool_calls[1].error
    assert result.message.tool_calls == result.tool_calls


def test_empty_response_raises():
    with pytest.raises(EmptyResponseError):
        wire_openai.complete(FakeClient(openai_response("  ")), CFG, [Message("user", "hi")], [])


def test_provider_failure_becomes_request_error():
    client = FakeClient(error=ValueError("boom"))
    with pytest.raises(RequestError, match="boom"):
        wire_openai.complete(client, CFG, [Message("user", "hi")], [])


def test_extras_are_replayed_only_when_asked():
    call = ToolCall("c1", "f", {})
    turn = Message(
        "assistant", None, [call], extras={"reasoning_details": [{"text": "t"}], "other": 1}
    )
    plain = wire_openai.to_messages([turn])[0]
    replayed = wire_openai.to_messages([turn], ("reasoning_details",))[0]
    assert "reasoning_details" not in plain and "other" not in replayed
    assert replayed["reasoning_details"] == [{"text": "t"}]


def test_parse_response_collects_requested_extras():
    message = NS(content="ok", tool_calls=None, reasoning_details=[NS(text="t")])
    response = NS(choices=[NS(message=message, finish_reason="stop")], usage=None, model="m")
    result = wire_openai.parse_response(response, CFG, ("reasoning_details",))
    assert result.extras == {"reasoning_details": [{"text": "t"}]}
    assert result.message.extras == result.extras


def test_no_choices_includes_provider_error_detail():
    response = NS(choices=[], error=NS(message="no capacity"), usage=None, model="m")
    with pytest.raises(RequestError, match="no capacity"):
        wire_openai.parse_response(response, CFG)
