"""HTTP API. A thin shell over the service.

Run:  uv run cpa-api        (docs at http://127.0.0.1:8000/docs)
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from cpa import __version__
from cpa.providers import (
    ChatResult,
    ConfigError,
    ProviderError,
)
from cpa.service import ChatRequest, ProviderInfo, Service, get_service

app = FastAPI(title="CPA", version=__version__)


@app.exception_handler(ProviderError)
def provider_error(_: Request, exc: ProviderError) -> JSONResponse:
    # Our own setup problem (bad id, missing key) is a 400; the provider failing is a 502.
    status = 400 if isinstance(exc, ConfigError) else 502
    kind = type(exc).__name__
    return JSONResponse({"error": kind, "detail": str(exc)}, status_code=status)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/providers")
def providers(service: Service = Depends(get_service)) -> list[ProviderInfo]:
    return service.providers()


@app.post("/chat")
def chat(request: ChatRequest, service: Service = Depends(get_service)) -> ChatResult:
    return service.chat(request)
