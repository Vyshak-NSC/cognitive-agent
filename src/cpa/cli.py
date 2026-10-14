"""Console commands: `cpa-api` and `cpa-ui`."""

from __future__ import annotations

import importlib.util
import sys

from cpa.settings import Settings


def api() -> None:
    import uvicorn

    settings = Settings.load()
    uvicorn.run("cpa.api:app", host=settings.api_host, port=settings.api_port)


def ui() -> None:
    from streamlit.web import cli as streamlit_cli

    script = importlib.util.find_spec("cpa.ui").origin  # path only; does not run the page
    sys.argv = ["streamlit", "run", script, *sys.argv[1:]]
    streamlit_cli.main()
