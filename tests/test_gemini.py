from __future__ import annotations

import pytest
from google.genai import types

from cpa.providers import (
    EmptyResponseError,
    Message,
    ProviderConfig,
    RequestError,
    ToolCall,
    ToolSpec,
)
from cpa.providers.adapters import gemini

CFG = ProviderConfig(provider_id="gemini", model="gemini-x", api_keys=("k1", "k2"))


def response(parts, finish="STOP"):
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(role="model", parts=parts), finish_reason=finish)
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=4, candidates_token_count=2, total_token_count=6
        ),
    )


def call_part(name="lookup", args=None, signature=b"sig-bytes"):
    return types.Part(
        function_call=types.FunctionCall(name=name, args=args or {"q": "x"}),
        thought_signature=signature,
    )


def test_system_is_separate_and_tool_results_merge_by_function_name():
    calls = [ToolCall("a", "lookup", {"q": 1}), ToolCall("b", "other", {})]
    system, contents = gemini.to_contents(
        [
            Message("system", "be brief"),
            Message("user", "go"),
            Message("assistant", "ok", calls),
            Message("tool", '{"found": true}', tool_call_id="a"),
            Message("tool", "plain text", tool_call_id="b"),
        ]
    )
    assert system == "be brief"
    assert [c.role for c in contents] == ["user", "model", "user"]
    responses = [p.function_response for p in contents[2].parts]
    assert [r.name for r in responses] == ["lookup", "other"]  # Gemini answers by name, not id
    assert responses[0].response == {"result": {"found": True}}
    assert responses[1].response == {"result": "plain text"}


def test_parse_response_text_calls_usage_and_signature_extras():
    result = gemini.parse_response(
        response([types.Part.from_text(text="hello "), call_part()]), CFG
    )
    assert result.text == "hello " and result.finish_reason == "STOP"
    assert result.tool_calls[0].name == "lookup" and result.tool_calls[0].arguments == {"q": "x"}
    assert result.usage.total_tokens == 6
    assert gemini.EXTRAS_KEY in result.extras  # kept because there is a tool call


def test_thought_signature_survives_a_full_round_trip():
    result = gemini.parse_response(response([call_part(signature=b"keep-me")]), CFG)
    history = [Message("user", "go"), result.message]
    _, contents = gemini.to_contents(history)
    replayed = contents[1].parts[0]
    assert replayed.thought_signature == b"keep-me" and replayed.function_call.name == "lookup"


def test_plain_text_replies_carry_no_extras():
    result = gemini.parse_response(response([types.Part.from_text(text="hi")]), CFG)
    assert result.extras == {}


def test_thought_parts_are_not_part_of_the_answer():
    parts = [
        types.Part(text="private reasoning", thought=True),
        types.Part.from_text(text="answer"),
    ]
    assert gemini.parse_response(response(parts), CFG).text == "answer"


def test_blocked_and_empty_replies_explain_why():
    blocked = types.GenerateContentResponse(
        candidates=[types.Candidate(content=None, finish_reason="SAFETY")]
    )
    with pytest.raises(EmptyResponseError, match="safety filter"):
        gemini.parse_response(blocked, CFG)
    with pytest.raises(EmptyResponseError, match="no candidates"):
        gemini.parse_response(types.GenerateContentResponse(candidates=[]), CFG)


# ----------------------------------------------------------- the adapter


class FakeGenai:
    """Replaces genai.Client; each key behaves as scripted."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def __call__(self, api_key):
        outer = self

        class Client:
            class models:
                @staticmethod
                def generate_content(model, contents, config):
                    outer.calls.append((api_key, model, config))
                    action = outer.script[api_key]
                    if isinstance(action, Exception):
                        raise action
                    return action

        return Client()


@pytest.fixture(autouse=True)
def fresh_clients():
    from cpa.providers.adapters import base

    base._CLIENTS.clear()


def test_adapter_rotates_to_the_next_key_on_quota(monkeypatch):
    ok = response([types.Part.from_text(text="from key 2")])
    fake = FakeGenai({"k1": RuntimeError("429 RESOURCE_EXHAUSTED quota"), "k2": ok})
    monkeypatch.setattr(gemini.genai, "Client", fake)
    result = gemini.create(CFG).chat([Message("system", "s"), Message("user", "hi")])
    assert result.text == "from key 2" and [c[0] for c in fake.calls] == ["k1", "k2"]
    config = fake.calls[1][2]
    assert config.system_instruction == "s" and config.http_options.timeout == 120_000


def test_adapter_does_not_rotate_on_ordinary_errors(monkeypatch):
    fake = FakeGenai({"k1": ValueError("invalid argument"), "k2": response([])})
    monkeypatch.setattr(gemini.genai, "Client", fake)
    with pytest.raises(RequestError, match="invalid argument"):
        gemini.create(CFG).chat([Message("user", "hi")])
    assert [c[0] for c in fake.calls] == ["k1"]


def test_adapter_reports_when_every_key_is_exhausted(monkeypatch):
    boom = RuntimeError("quota exceeded")
    monkeypatch.setattr(gemini.genai, "Client", FakeGenai({"k1": boom, "k2": boom}))
    with pytest.raises(RequestError, match="All 2 configured API keys"):
        gemini.create(CFG).chat([Message("user", "hi")])


def test_adapter_sends_tools_as_json_schema(monkeypatch):
    fake = FakeGenai({"k1": response([types.Part.from_text(text="ok")])})
    monkeypatch.setattr(gemini.genai, "Client", fake)
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    gemini.create(CFG).chat([Message("user", "hi")], [ToolSpec("lookup", "find", schema)])
    declaration = fake.calls[0][2].tools[0].function_declarations[0]
    assert declaration.name == "lookup" and declaration.parameters_json_schema == schema
