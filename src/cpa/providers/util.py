"""Small helpers shared by both wire formats."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)


def parse_arguments(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Tool-call arguments as (dict, error). Tolerates code fences; never raises."""
    if isinstance(raw, dict):
        return raw, None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}, None
    text = raw if isinstance(raw, str) else str(raw)
    for candidate in (text, _FENCE.sub("", text)):
        try:
            value = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        return (value if isinstance(value, dict) else {}), None
    return {}, f"Tool arguments were not valid JSON: {text[:200]!r}"


def to_plain(value: Any) -> Any:
    """Turn SDK objects (pydantic models, namespaces) into plain JSON-able data."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return {k: to_plain(v) for k, v in vars(value).items()}
    return value
