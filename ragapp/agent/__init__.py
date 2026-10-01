"""Agent package.

Keep this package initializer free of eager imports.

Agent submodules such as retrieval and tracing can import
``ragapp.agent.trace`` without causing ``agent.loop`` to load
and create circular imports.
"""


def run_agent(*args, **kwargs):
    """Lazily import and call the agent runner."""
    from ragapp.agent.loop import run_agent as _run_agent

    return _run_agent(*args, **kwargs)