"""Provider-neutral tool-calling dispatch."""
from ragapp.config import load_project_config

def _module(store):
    name = load_project_config(store).get("provider", {}).get("name", "gemini").lower()
    if name == "openrouter":
        from ragapp.llm.tool_calling import openrouter
        return openrouter
    if name == "gemini":
        from ragapp.llm.tool_calling import gemini
        return gemini
    if name in {"azure", "azure_openai"}:
        from ragapp.llm.tool_calling import azure_openai
        return azure_openai
    raise RuntimeError(
        f"Provider '{name}' is configured but no tool-calling adapter is enabled."
    )

def to_provider_contents(transcript, store=None):
    return _module(store).to_provider_contents(transcript, store=store) if store else _module(store).to_provider_contents(transcript)

def generate_step(contents, function_declarations, system_instruction=None, store=None):
    return _module(store).generate_step(contents, function_declarations, system_instruction, store)

def build_function_response_content(name, result, call_id=None, store=None):
    return _module(store).build_function_response_content(name, result, call_id)

__all__ = ["to_provider_contents", "generate_step", "build_function_response_content"]
