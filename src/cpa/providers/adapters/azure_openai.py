"""Azure OpenAI (GPT deployments on Azure AI Foundry): the OpenAI adapter on an Azure URL."""

from __future__ import annotations

from cpa.providers.adapters import azure, openai
from cpa.providers.adapters.base import WireAdapter
from cpa.providers.types import ProviderConfig


def create(cfg: ProviderConfig) -> WireAdapter:
    return openai.create(cfg, base_url=azure.base_url(cfg))
