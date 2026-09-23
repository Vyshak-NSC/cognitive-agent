"""Central Mermaid normalization for chat and file previews."""
from __future__ import annotations

import re


_FENCED = re.compile(r"^```(?:mermaid)?[ \t]*\n?(.*?)\n?```$", re.IGNORECASE | re.DOTALL)


def normalize_mermaid_source(content: str) -> str:
    """Return Mermaid source without an optional Markdown code fence."""
    text = (content or "").strip()
    if not text:
        raise ValueError("Empty Mermaid diagram.")

    match = _FENCED.match(text)
    if match:
        text = match.group(1).strip()

    if not text:
        raise ValueError("Empty Mermaid diagram.")
    return text


def process_mermaid_content(content: str) -> str:
    """Return valid Markdown containing a single Mermaid code block."""
    source = normalize_mermaid_source(content)
    return f"```mermaid\n{source}\n```"
