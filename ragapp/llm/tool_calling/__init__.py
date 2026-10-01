"""Provider-neutral tool-calling dispatch.

The active adapter is the module named by the project's configured provider;
the provider list lives in ``ragapp.llm.provider.PROVIDER_SPECS``. Every adapter
exposes the same functions: ``to_provider_contents``, ``generate_step``,
``build_function_response_content``, ``generate_json`` and ``model_name``.
"""


def _adapter(store):
    from ragapp.llm.provider import adapter_for

    return adapter_for(store)


def to_provider_contents(transcript, store=None):
    return _adapter(store).to_provider_contents(transcript, store=store)


def generate_step(contents, function_declarations, system_instruction=None, store=None):
    return _adapter(store).generate_step(contents, function_declarations, system_instruction, store)


def build_function_response_content(name, result, call_id=None, store=None):
    return _adapter(store).build_function_response_content(name, result, call_id)


__all__ = ["to_provider_contents", "generate_step", "build_function_response_content"]
