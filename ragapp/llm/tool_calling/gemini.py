from google.genai import types
from ragapp.settings import CHAT_MODEL, DEFAULT_CHAT_MODEL
from ragapp.config import get_api_keys, resolve_model

class EmptyResponseError(RuntimeError):
    """Raised when Gemini returns no usable content."""

def _role_for(role):
    return "model" if role == "assistant" else "user"

def to_provider_contents(transcript, store=None):
    return [
        types.Content(
            role=_role_for(m["role"]),
            parts=[types.Part.from_text(text=m["content"])]
        )
        for m in transcript
        if isinstance(m, dict)
    ]

def to_gemini_tool(declarations):
    return types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name=d["name"],
            description=d.get("description", ""),
            parameters=d.get("parameters", {"type": "object", "properties": {}})
        )
        for d in declarations
    ])

def _clients(store):
    keys = get_api_keys(store, "gemini")
    if not keys:
        raise RuntimeError(
            "No Gemini API key configured. Add one in Project Settings or set "
            "GEMINI_API_KEY (and optionally GEMINI_API_KEY_2 / GEMINI_API_KEY_3 for fallback)."
        )
    from google import genai
    return [genai.Client(api_key=k) for k in keys]

def _with_fallback(store, fn):
    """Try the request with each configured Gemini API key in order."""
    keys = get_api_keys(store, "gemini")
    if not keys:
        raise RuntimeError(
            "No Gemini API keys configured."
        )
    from google import genai
    last_exc = None
    for key in keys:
        client = genai.Client(api_key=key)
        try:
            return fn(client)
        except EmptyResponseError:
            raise
        except Exception as exc:
            last_exc = exc
            error_text = str(exc).lower()
            # Gemini quota/rate-limit/auth failures mean this key
            # cannot service the request. Move immediately to the next key.
            key_exhausted = any(term in error_text for term in (
                "quota",
                "resource_exhausted",
                "rate limit",
                "ratelimit",
                "too many requests",
                "429",
                "unauthenticated",
                "permission denied",
                "api key",
            ))
            if key_exhausted:
                continue
            # Other request/model errors should not silently burn
            # through all API keys.
            raise
    raise RuntimeError(
        f"All configured Gemini API keys are exhausted or unavailable. "
        f"Last error: {last_exc}"
    )
def generate_step(contents, function_declarations, system_instruction=None, store=None):
    if store is None:
        raise RuntimeError("A project store is required for Gemini generation.")
    model = resolve_model(store, CHAT_MODEL, DEFAULT_CHAT_MODEL)
    cfg = types.GenerateContentConfig(
        tools=[to_gemini_tool(function_declarations)] if function_declarations else None,
        system_instruction=system_instruction,
    )
    response = _with_fallback(store, lambda client: client.models.generate_content(
        model=model,
        contents=contents,
        config=cfg,
    ))
    if not response.candidates:
        raise EmptyResponseError("The model returned no candidates.")
    candidate = response.candidates[0]
    finish_reason = str(getattr(candidate, "finish_reason", "") or "")
    parts = candidate.content.parts if candidate.content is not None else None
    if not parts:
        hint = {
            "SAFETY": " (blocked by a safety filter)",
            "PROHIBITED_CONTENT": " (blocked as prohibited content)",
            "RECITATION": " (blocked for recitation)",
            "MAX_TOKENS": " (hit the output-token limit)",
        }.get(finish_reason, f" (finish_reason={finish_reason})" if finish_reason else "")
        raise EmptyResponseError(
            f"The model returned an empty response{hint}. "
            "Try rephrasing the request or asking again."
        )
    calls = [
        {"name": p.function_call.name, "args": dict(p.function_call.args)}
        for p in parts
        if getattr(p, "function_call", None)
    ]
    return {
        "function_calls": calls,
        "text": response.text if not calls else None,
        "model_content": candidate.content,
    }

def generate_json(store, prompt, model=None):
    if store is None:
        raise RuntimeError("A project store is required for Gemini generation.")
    model = model or resolve_model(store, CHAT_MODEL, DEFAULT_CHAT_MODEL)
    response = _with_fallback(store, lambda client: client.models.generate_content(
        model=model,
        contents=[types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt)]
        )],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    ))
    text = response.text or ""
    if not text.strip():
        raise RuntimeError("Gemini returned an empty JSON response.")
    return text

def build_function_response_content(name, result, call_id=None):
    return types.Content(
        role="user",
        parts=[types.Part.from_function_response(
            name=name,
            response={"result": result}
        )]
    )