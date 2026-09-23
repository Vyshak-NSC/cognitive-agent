"""
The Tool contract every registered capability must satisfy.

To add a new tool later: create a module under `ragapp/tools/`
exposing a `build_<name>_tool(...)` factory that returns a `Tool`,
then add it to `build_default_tools()` in `ragapp/tools/__init__.py`.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict


@dataclass
class Tool:
    name: str
    description: str
    # JSON Schema describing the tool's arguments, e.g.:
    # {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    parameters: Dict[str, Any]
    # Called as handler(**args). Must return a JSON-serializable value.
    handler: Callable[..., Any]
