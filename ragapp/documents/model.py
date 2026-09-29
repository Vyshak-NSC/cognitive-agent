"""Small format-neutral document intermediate representation (IR)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class Block:
    kind: str
    text: str = ""
    level: int | None = None
    rows: list[list[str]] = field(default_factory=list)
    ordered: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class DocumentModel:
    title: str = ""
    blocks: list[Block] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def source_text(self) -> str:
        parts: list[str] = []
        for block in self.blocks:
            if block.text:
                parts.append(block.text)
            for row in block.rows:
                parts.extend(str(cell) for cell in row if str(cell).strip())
        return "\n".join(parts)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for block in self.blocks:
            out[block.kind] = out.get(block.kind, 0) + 1
        return out
