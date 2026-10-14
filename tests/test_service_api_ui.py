from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

import cpa.service as service_module
from conftest import FakeProvider
from cpa import api as api_module
from cpa.providers import ConfigError, EmptyResponseError, Message, RequestError
from cpa.service import ChatRequest, Service
from cpa.settings import Settings

UI = str(Path(__file__).resolve().parent.parent / "src" / "cpa" / "ui.py")


@pytest.fixture
def fake_service(settings):
    built: list[FakeProvider] = []

    def build(config):
        built.append(FakeProvider(config))
        return built[-1]

    service = Service(settings, build)
    service.built = built
    return service


def test_service_lists_providers_with_status():
    service = Service(Settings.load({"GEMINI_API_KEY": "k"}))
    status = {p.id: p.configured for p in service.providers()}
    assert status["gemini"] is True and status["openai"] is False


def test_service_uses_default_provider_and_adds_system(fake_service):
    result = fake_service.chat(ChatRequest(messages=[Message("user", "hi")], system="be brief"))
    assert result.provider == "gemini"
    seen = fake_service.built[0].seen
    assert [m.role for m in seen] == ["system", "user"]


# ------------------------------------------------------------------- API


@pytest.fixture
def client(fake_service):
    api_module.app.dependency_overrides[service_module.get_service] = lambda: fake_service
    yield TestClient(api_module.app)
    api_module.app.dependency_overrides.clear()


def test_api_health_and_providers(client):
    assert client.get("/health").json()["status"] == "ok"
    ids = [p["id"] for p in client.get("/providers").json()]
    assert ids[0] == "gemini" and len(ids) == 6


def test_api_chat_round_trip(client):
    body = {"provider": "anthropic", "messages": [{"role": "user", "content": "hi"}]}
    data = client.post("/chat", json=body).json()
    assert data["text"] == "hello" and data["provider"] == "anthropic"
    assert data["usage"]["total_tokens"] == 5


def test_api_passes_extras_through_both_ways(client, fake_service):
    body = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "x", "extras": {"gemini_parts": [{"text": "x"}]}},
            {"role": "user", "content": "more"},
        ]
    }
    assert client.post("/chat", json=body).status_code == 200
    sent = fake_service.built[-1].seen
    assert sent[1].extras == {"gemini_parts": [{"text": "x"}]}


def test_api_rejects_a_tool_message_without_id(client):
    body = {"messages": [{"role": "tool", "content": "x"}]}
    assert client.post("/chat", json=body).status_code == 422


@pytest.mark.parametrize(
    ("error", "status"),
    [(ConfigError("no key"), 400), (RequestError("down"), 502), (EmptyResponseError("empty"), 502)],
)
def test_api_maps_errors(settings, error, status):
    def build(config):
        raise error

    api_module.app.dependency_overrides[service_module.get_service] = lambda: Service(
        settings, build
    )
    try:
        response = TestClient(api_module.app).post(
            "/chat", json={"messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        api_module.app.dependency_overrides.clear()
    assert response.status_code == status
    assert response.json()["error"] == type(error).__name__


# -------------------------------------------------------------------- UI


def test_ui_chat_flow(monkeypatch, fake_service):
    monkeypatch.setattr(service_module, "get_service", lambda: fake_service)
    app = AppTest.from_file(UI, default_timeout=30).run()
    assert not app.exception
    assert len(app.sidebar.selectbox[0].options) == 6

    app.chat_input[0].set_value("hi there").run()
    assert not app.exception
    roles = [m.name for m in app.chat_message]
    assert roles == ["user", "assistant"]
    assert "hello" in app.chat_message[1].markdown[0].value
    assert fake_service.built[-1].seen[-1].content == "hi there"


def test_ui_shows_error_and_keeps_history_clean(monkeypatch, settings):
    def build(config):
        raise RequestError("provider down")

    monkeypatch.setattr(service_module, "get_service", lambda: Service(settings, build))
    app = AppTest.from_file(UI, default_timeout=30).run()
    app.chat_input[0].set_value("hi").run()
    assert "provider down" in app.error[0].value
    assert app.session_state["turns"] == []
