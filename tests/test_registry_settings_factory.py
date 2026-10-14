from __future__ import annotations

import pytest

from cpa.providers import SPECS, ConfigError, build_provider, get_spec
from cpa.providers.factory import resolve_base_url
from cpa.settings import Settings


def test_six_providers_exist():
    assert list(SPECS) == [
        "gemini",
        "openrouter",
        "openai",
        "anthropic",
        "azure_openai",
        "azure_anthropic",
    ]


def test_unknown_provider():
    with pytest.raises(ConfigError, match="Known providers"):
        get_spec("nope")


def test_settings_missing_and_models():
    s = Settings.load({"AZURE_FOUNDRY_API_KEY": "k", "OPENAI_MODEL": "custom"})
    assert s.missing("openai") == ["OPENAI_API_KEY"]
    assert s.missing("azure_openai") == ["AZURE_FOUNDRY_ENDPOINT"]
    assert s.missing("gemini") == ["GEMINI_API_KEY"]
    assert s.default_model("openai") == "custom"
    assert s.default_model("anthropic") == get_spec("anthropic").default_model
    # a request-level model wins over the environment
    assert s.provider_config("openai", model="x").model == "x"


def test_bad_default_provider_fails_early():
    with pytest.raises(ConfigError):
        Settings.load({"CPA_PROVIDER": "gemni"})


def test_azure_endpoint_is_normalised():
    spec = get_spec("azure_openai")
    expected = "https://r.services.ai.azure.com/openai/v1"
    for pasted in (
        "r.services.ai.azure.com",
        "https://r.services.ai.azure.com/",
        "https://r.services.ai.azure.com/openai/v1/chat",
    ):
        assert resolve_base_url(spec, pasted) == expected
    assert resolve_base_url(get_spec("azure_anthropic"), "r.services.ai.azure.com").endswith(
        "/anthropic"
    )
    with pytest.raises(ConfigError, match="AZURE_FOUNDRY_ENDPOINT"):
        resolve_base_url(spec, None)


@pytest.mark.parametrize(
    ("provider_id", "client_type", "url_part"),
    [
        ("gemini", "OpenAI", "generativelanguage.googleapis.com"),
        ("openrouter", "OpenAI", "openrouter.ai"),
        ("openai", "OpenAI", "api.openai.com"),
        ("anthropic", "Anthropic", "api.anthropic.com"),
        ("azure_openai", "OpenAI", "my-res.services.ai.azure.com/openai/v1"),
        ("azure_anthropic", "AnthropicFoundry", "my-res.services.ai.azure.com/anthropic"),
    ],
)
def test_each_provider_builds_the_right_client(settings, provider_id, client_type, url_part):
    provider = build_provider(settings.provider_config(provider_id))
    assert type(provider.client).__name__ == client_type
    assert url_part in str(provider.client.base_url)


def test_missing_key_is_a_config_error():
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        build_provider(Settings.load({}).provider_config("anthropic"))
