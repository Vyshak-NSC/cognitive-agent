from __future__ import annotations

import pytest

from cpa.providers import SPECS, ConfigError, build_provider, get_spec
from cpa.providers.adapters import azure
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
    assert s.provider_config("openai", model="x").model == "x"  # a request wins over the env


def test_multi_key_providers_read_numbered_keys_in_order():
    s = Settings.load(
        {
            "GEMINI_API_KEY": "a",
            "GEMINI_API_KEY_3": "c",
            "GEMINI_API_KEY_2": "b",
            "OPENAI_API_KEY_2": "x",
        }
    )
    assert s.keys("gemini") == ("a", "b", "c")
    assert s.keys("openai") == ()  # single-key providers ignore numbered variables
    assert s.provider_config("gemini").api_key == "a"


def test_provider_options_come_from_the_environment_with_defaults():
    s = Settings.load({"OPENROUTER_FALLBACK_MODELS": "a/b, c/d", "OPENROUTER_SITE_NAME": "Mine"})
    options = s.provider_config("openrouter").options
    assert options["fallback_models"] == "a/b, c/d" and options["site_name"] == "Mine"
    assert options["require_parameters"] == "false" and options["retry_budget_seconds"] == "45"
    assert s.provider_config("openai").options == {}


def test_bad_default_provider_fails_early():
    with pytest.raises(ConfigError):
        Settings.load({"CPA_PROVIDER": "gemni"})


def test_azure_endpoint_is_normalised(settings):
    expected = "https://my-res.services.ai.azure.com/openai/v1"
    for pasted in (
        "my-res.services.ai.azure.com",
        "https://my-res.services.ai.azure.com/",
        "https://my-res.services.ai.azure.com/openai/v1/chat",
    ):
        cfg = settings.provider_config("azure_openai")
        cfg.endpoint = pasted
        assert azure.base_url(cfg) == expected
    anthropic = settings.provider_config("azure_anthropic")
    assert azure.base_url(anthropic).endswith("/anthropic")
    no_endpoint = Settings.load({"AZURE_FOUNDRY_API_KEY": "k"}).provider_config("azure_openai")
    with pytest.raises(ConfigError, match="AZURE_FOUNDRY_ENDPOINT"):
        azure.base_url(no_endpoint)


@pytest.mark.parametrize(
    ("provider_id", "adapter_type", "url_part"),
    [
        ("gemini", "GeminiAdapter", None),
        ("openrouter", "OpenRouterAdapter", None),
        ("openai", "WireAdapter", "api.openai.com"),
        ("anthropic", "WireAdapter", "api.anthropic.com"),
        ("azure_openai", "WireAdapter", "my-res.services.ai.azure.com/openai/v1"),
        ("azure_anthropic", "WireAdapter", "my-res.services.ai.azure.com/anthropic"),
    ],
)
def test_each_provider_builds_its_own_adapter(settings, provider_id, adapter_type, url_part):
    adapter = build_provider(settings.provider_config(provider_id)).adapter
    assert type(adapter).__name__ == adapter_type
    if url_part:
        assert url_part in str(adapter.client.base_url)


def test_wire_adapters_use_the_right_wire_and_sdk(settings):
    pairs = {
        "openai": ("wire_openai", "OpenAI"),
        "azure_openai": ("wire_openai", "OpenAI"),
        "anthropic": ("wire_anthropic", "Anthropic"),
        "azure_anthropic": ("wire_anthropic", "AnthropicFoundry"),
    }
    for provider_id, (wire, client) in pairs.items():
        adapter = build_provider(settings.provider_config(provider_id)).adapter
        assert adapter.wire.__name__.endswith(wire) and type(adapter.client).__name__ == client


def test_missing_key_is_a_config_error():
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        build_provider(Settings.load({}).provider_config("anthropic"))
