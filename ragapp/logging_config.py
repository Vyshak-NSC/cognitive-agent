"""Central logging setup for the ragapp process.

`configure_logging()` is called once at import time by ragapp.agent.loop
(and safe to call again elsewhere). It is idempotent: calling it more than
once will not add duplicate handlers.
"""
from __future__ import annotations

import logging
import os

_CONFIGURED = False


def configure_logging(level: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()

    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(handler)

    root.setLevel(getattr(logging, log_level, logging.INFO))

    # Keep noisy third-party libraries quieter than the app's own logs.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True