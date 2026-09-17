"""Core Mermaid extraction, normalization, and structural validation.

UI-independent Mermaid processing. LLM output is normalized and structurally
checked before it is passed to a renderer.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_MERMAID_FENCE_RE = re.compile(
    r"```mermaid\s*\n(.*?)```",
    re.IGNORECASE | re.DOTALL,
)

_DIRECTION_RE = re.compile(
    r"^(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)\s*$",
    re.IGNORECASE,
)
_SUBGRAPH_RE = re.compile(r"^subgraph(?:\s+|$)", re.IGNORECASE)
_END_RE = re.compile(r"^end$", re.IGNORECASE)
_SEPARATOR_RE = re.compile(r"^[\s\-_=~*#]+$")


@dataclass(frozen=True)
class MermaidValidation:
    valid: bool
    reason: str = ""


def _normalize_diagram(diagram: str) -> str:
    """Normalize Unicode/control corruption without inventing Mermaid syntax."""
    diagram = unicodedata.normalize("NFKC", diagram)
    diagram = "".join(
        ch
        for ch in diagram
        if unicodedata.category(ch) not in {"Cc", "Cf"} or ch in "\n\t"
    )

    diagram = re.sub(
        r"(?:color|fill|stroke)\s*:\s*[^#\n;]*#([0-9a-fA-F]{6})",
        lambda m: f"{m.group(0).split(':', 1)[0]}:#{m.group(1)}",
        diagram,
    )

    lines = []
    for line in diagram.splitlines():
        if "style " in line.lower() or "classDef" in line:
            line = re.sub(r"[^\x00-\x7F]", "", line)
        lines.append(line.rstrip())

    return "\n".join(lines).strip()


def extract_mermaid_blocks(content: str) -> list[str]:
    """Return normalized Mermaid source blocks without Markdown fences."""
    return [
        _normalize_diagram(match.group(1))
        for match in _MERMAID_FENCE_RE.finditer(content or "")
    ]


def validate_mermaid(diagram: str) -> MermaidValidation:
    """Validate the Mermaid structures that can be checked deterministically."""
    lines = [line.strip() for line in diagram.splitlines() if line.strip()]

    if not lines:
        return MermaidValidation(False, "Empty Mermaid diagram.")

    if not _DIRECTION_RE.match(lines[0]):
        return MermaidValidation(
            False,
            "Diagram must begin with `flowchart TD` or another valid direction.",
        )

    subgraph_depth = 0

    for number, line in enumerate(lines[1:], start=2):
        if _SEPARATOR_RE.fullmatch(line):
            return MermaidValidation(
                False,
                f"Invalid standalone separator on line {number}.",
            )

        if _SUBGRAPH_RE.match(line):
            subgraph_depth += 1
            continue

        if _END_RE.fullmatch(line):
            if subgraph_depth == 0:
                return MermaidValidation(
                    False,
                    f"Unexpected `end` on line {number}.",
                )
            subgraph_depth -= 1
            continue

        # Catch common malformed node declarations without attempting to
        # implement the complete Mermaid grammar.
        for opening, closing, label in (
            ("[", "]", "[]"),
            ("(", ")", "()"),
            ("{", "}", "{}"),
        ):
            if line.count(opening) != line.count(closing):
                return MermaidValidation(
                    False,
                    f"Unbalanced {label} on line {number}.",
                )

    if subgraph_depth:
        return MermaidValidation(
            False,
            "One or more subgraphs are missing their closing `end`.",
        )

    return MermaidValidation(True)


def _render_block(diagram: str) -> str:
    return f"```mermaid\n{diagram}\n```"


def process_mermaid_content(content: str) -> str:
    """Process Mermaid blocks before they reach the UI renderer.

    Valid Mermaid is rendered normally. Invalid Mermaid is kept as text with a
    local diagnostic, so Streamlit's Mermaid parser never receives malformed
    source.
    """
    if not content:
        return ""

    def replace(match: re.Match[str]) -> str:
        diagram = _normalize_diagram(match.group(1))
        result = validate_mermaid(diagram)

        if result.valid:
            return _render_block(diagram)

        diagnostic = (
            "\n\n> Mermaid diagram was not rendered because its syntax is "
            f"invalid: {result.reason}\n\n"
        )
        return f"```text\n{diagram}\n```{diagnostic}"

    return _MERMAID_FENCE_RE.sub(replace, content)
