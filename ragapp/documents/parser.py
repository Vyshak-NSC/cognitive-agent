"""Parsers that preserve source order and semantic structure."""
from __future__ import annotations
import re
import docx
from docx.table import Table
from docx.text.paragraph import Paragraph
from .model import Block, DocumentModel

_HEADING_RE = re.compile(r"heading\s*(\d+)", re.I)
_LIST_RE = re.compile(r"(?:list|bullet|number)", re.I)


def _paragraph_block(p: Paragraph) -> Block | None:
    text = p.text.strip()
    if not text:
        return None
    style = p.style.name if p.style else ""
    match = _HEADING_RE.search(style)
    if match:
        return Block("heading", text=text, level=max(1, min(6, int(match.group(1)))), metadata={"style": style})
    if _LIST_RE.search(style):
        ordered = "number" in style.lower()
        return Block("list_item", text=text, ordered=ordered, metadata={"style": style})
    # Common note/callout glyphs are preserved semantically for styled rendering.
    if text.startswith(("📌", "NOTE:", "Note:", "WARNING:", "Warning:")):
        return Block("callout", text=text, metadata={"style": style})
    return Block("paragraph", text=text, metadata={"style": style})


def _table_block(table: Table) -> Block:
    rows: list[list[str]] = []
    for row in table.rows:
        values: list[str] = []
        last_tc = None
        for cell in row.cells:
            if cell._tc is last_tc:
                continue
            last_tc = cell._tc
            values.append(cell.text.strip())
        rows.append(values)
    return Block("table", rows=rows)


def parse_docx(path) -> DocumentModel:
    doc = docx.Document(path)
    blocks: list[Block] = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            block = _paragraph_block(Paragraph(child, doc))
            if block:
                blocks.append(block)
        elif tag == "tbl":
            blocks.append(_table_block(Table(child, doc)))
    title = next((b.text for b in blocks if b.kind == "heading" and b.level == 1), "")
    if not title:
        title = next((b.text for b in blocks if b.kind in {"paragraph", "heading"}), "")
    return DocumentModel(title=title, blocks=blocks, metadata={"source_format": "docx", "sections": len(doc.sections)})
