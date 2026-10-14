"""OpenRouter adapter, run through the real SDK against a fake network."""

from __future__ import annotations

import json

import httpx
import pytest
from openrouter import OpenRouter

from cpa.providers import ConfigError, Message, ProviderConfig, RequestError, ToolCall, ToolSpec
from cpa.providers.adapters import base
from cpa.providers.adapters import openrouter as adapter_module


def good(content="hi", **message):
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 1,
        "model": "vendor/served",
        "system_fingerprint": "fp",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content, **message},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }


class Network:
    """Fake OpenRouter: records every request and answers per API key."""

    def __init__(self, answers):
        self.answers, self.requests = answers, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        key = request.headers["authorization"].removeprefix("Bearer ")
        self.requests.append(
            {"key": key, "headers": request.headers, "body": json.loads(request.content)}
        )
        status, payload = self.answers[key]
        return httpx.Response(status, json=payload)


@pytest.fixture
def network(monkeypatch):
    base._CLIENTS.clear()
    holder = {}

    def install(answers):
        holder["net"] = Network(answers)
        transport = httpx.MockTransport(holder["net"].handler)
        monkeypatch.setattr(
            adapter_module,
            "OpenRouter",
            lambda **kw: OpenRouter(**kw, client=httpx.Client(transport=transport)),
        )
        return holder["net"]

    return install


def cfg(**kw):
    kw.setdefault("api_keys", ("k1",))
    options = {"retry_budget_seconds": "45", **kw.pop("options", {})}
    return ProviderConfig(provider_id="openrouter", model="vendor/model", options=options, **kw)


def chat(config, tools=(), messages=None):
    return adapter_module.create(config).chat(messages or [Message("user", "hi")], tools)


def test_basic_chat_headers_and_result(network):
    net = network({"k1": (200, good("hello"))})
    config = cfg(options={"site_url": "https://app.test", "site_name": "Mine"})
    result = chat(config)
    request = net.requests[0]
    assert (
        result.text == "hello"
        and result.model == "vendor/served"
        and result.usage.total_tokens == 3
    )
    assert request["body"]["model"] == "vendor/model" and "models" not in request["body"]
    assert request["headers"]["http-referer"] == "https://app.test"
    assert request["headers"]["x-openrouter-title"] == "Mine"


def test_fallback_models_become_a_server_side_chain(network):
    net = network({"k1": (200, good())})
    chat(cfg(options={"fallback_models": "a/b, vendor/model, c/d"}))
    body = net.requests[0]["body"]
    assert body["models"] == ["vendor/model", "a/b", "c/d"] and "model" not in body


def test_require_parameters_only_applies_when_tools_are_sent(network):
    net = network({"k1": (200, good())})
    options = {"require_parameters": "true", "routing": '{"order": ["x"]}'}
    chat(cfg(options=options))
    assert net.requests[0]["body"]["provider"] == {"order": ["x"]}
    chat(cfg(options=options), tools=[ToolSpec("f")])
    assert net.requests[1]["body"]["provider"] == {"require_parameters": True, "order": ["x"]}


def test_rotates_to_the_next_key_on_rate_limit_or_no_credits(network):
    net = network(
        {
            "k1": (429, {"error": {"message": "rate limited", "code": 429}}),
            "k2": (402, {"error": {"message": "no credits", "code": 402}}),
            "k3": (200, good("third time")),
        }
    )
    result = chat(cfg(api_keys=("k1", "k2", "k3")))
    assert result.text == "third time" and [r["key"] for r in net.requests] == ["k1", "k2", "k3"]


def test_a_bad_request_does_not_rotate(network):
    net = network({"k1": (400, {"error": {"message": "bad", "code": 400}}), "k2": (200, good())})
    with pytest.raises(RequestError):
        chat(cfg(api_keys=("k1", "k2")))
    assert [r["key"] for r in net.requests] == ["k1"]


def test_a_sloppy_backend_response_is_salvaged(network):
    # Missing id, object, created, model... the SDK's strict schema rejects this.
    sloppy = {"choices": [{"message": {"role": "assistant", "content": "salvaged"}}]}
    network({"k1": (200, sloppy)})
    assert chat(cfg()).text == "salvaged"


def test_reasoning_blocks_come_back_and_are_replayed_with_tool_calls(network):
    tool_call = {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
    reasoning = [{"type": "reasoning.text", "text": "thinking"}]
    net = network({"k1": (200, good(None, tool_calls=[tool_call], reasoning_details=reasoning))})
    result = chat(cfg(), tools=[ToolSpec("f")])
    assert result.tool_calls[0].name == "f"
    assert result.extras["reasoning_details"][0]["text"] == "thinking"

    history = [
        Message("user", "go"),
        result.message,
        Message("tool", "42", tool_call_id="c1"),
    ]
    chat(cfg(), tools=[ToolSpec("f")], messages=history)
    sent = net.requests[1]["body"]["messages"]
    assert sent[1]["reasoning_details"][0]["text"] == "thinking"
    assert sent[2] == {"role": "tool", "tool_call_id": "c1", "content": "42"}


def test_a_model_name_from_another_provider_is_refused(network):
    network({"k1": (200, good())})
    config = cfg()
    config.model = "gemini-3.5-flash"
    with pytest.raises(ConfigError, match="vendor/model"):
        chat(config)


def test_invalid_routing_json_is_a_config_error(network):
    network({"k1": (200, good())})
    with pytest.raises(ConfigError, match="OPENROUTER_ROUTING"):
        chat(cfg(options={"routing": "{oops"}))


def test_tool_call_ids_survive_translation():
    from cpa.providers import wire_openai

    wire = wire_openai.to_messages([Message("assistant", None, [ToolCall("c9", "f", {"a": 1})])])
    assert wire[0]["tool_calls"][0]["id"] == "c9"
