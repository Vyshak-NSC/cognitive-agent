"""Presentation-native renderer for source-preserving document transformations.

The renderer deliberately does not summarize the Document IR. It plans slides from
semantic blocks, splitting dense content across additional slides and repeating
structured table headers so a conversion request cannot silently lose source data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pptx import Presentation
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

from .themes import get_theme


@dataclass
class _ContentItem:
    text: str
    level: int = 0
    kind: str = "paragraph"


def _rgb(color):
    return RGBColor(int(color.red * 255), int(color.green * 255), int(color.blue * 255))


def _split_text(text: str, limit: int = 620) -> list[str]:
    """Split long prose at sentence/word boundaries without dropping text."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return [text] if text else []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > limit:
            out.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        out.append(current)
    # Extremely long sentence: split only at word boundaries.
    final: list[str] = []
    for chunk in out:
        while len(chunk) > limit:
            cut = chunk.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            final.append(chunk[:cut].strip())
            chunk = chunk[cut:].strip()
        if chunk:
            final.append(chunk)
    return final


def _add_textbox(slide, x, y, w, h, text="", size=18, bold=False, color=None, align=PP_ALIGN.LEFT):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = shape.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.TOP
    p = tf.paragraphs[0]
    p.text = text
    p.alignment = align
    p.font.size = Pt(size)
    p.font.bold = bold
    if color:
        p.font.color.rgb = color
    return shape


def _add_footer(slide, number, theme):
    _add_textbox(
        slide, 0.65, 7.05, 12.0, 0.25,
        f"{theme.header_text or 'Document'}  •  {number}",
        size=8.5, color=_rgb(theme.accent), align=PP_ALIGN.RIGHT,
    )


def _add_title(slide, title, theme, section=False):
    accent = _rgb(theme.accent)
    if section:
        band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(7.5))
        band.fill.solid()
        band.fill.fore_color.rgb = accent
        band.line.fill.background()
        _add_textbox(slide, 0.9, 2.35, 11.55, 1.6, title, size=30, bold=True, color=RGBColor(255, 255, 255))
        _add_textbox(slide, 0.92, 4.0, 10.8, 0.5, "Section", size=12, color=RGBColor(235, 240, 244))
        return
    _add_textbox(slide, 0.7, 0.42, 11.9, 0.7, title, size=25 if theme.name == "fantasy_codex" else 23, bold=True, color=accent)
    rule = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.72), Inches(1.18), Inches(1.05), Inches(0.05))
    rule.fill.solid(); rule.fill.fore_color.rgb = accent; rule.line.fill.background()


def _add_title_slide(prs, model, theme):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    accent = _rgb(theme.accent)
    _add_textbox(slide, 0.9, 1.65, 11.4, 1.25, model.title or "Document", size=32, bold=True, color=accent)
    _add_textbox(slide, 0.95, 3.0, 10.8, 0.65, "Source-preserving presentation", size=16, color=accent)
    _add_textbox(slide, 0.95, 5.85, 11.0, 0.5, theme.name.replace("_", " ").title(), size=11, color=accent)
    return slide


def _add_content_slide(prs, title, items, theme, slide_number):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_title(slide, title, theme)
    box = slide.shapes.add_textbox(Inches(0.82), Inches(1.42), Inches(11.65), Inches(5.35))
    tf = box.text_frame
    tf.clear(); tf.word_wrap = True
    for idx, item in enumerate(items):
        p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
        p.text = item.text
        p.level = min(item.level, 2)
        p.font.size = Pt(18 if item.level == 0 else 15)
        p.font.bold = item.kind == "callout" or item.kind == "subheading"
        p.space_after = Pt(9)
        p.font.color.rgb = _rgb(theme.accent) if item.kind == "subheading" else RGBColor(40, 40, 40)
    _add_footer(slide, slide_number, theme)
    return slide


def _add_table_slides(prs, title, rows, theme, slide_number, rows_per_slide=9):
    if not rows:
        return slide_number
    header = rows[0]
    body = rows[1:] if len(rows) > 1 else []
    cols = max(1, max(len(r) for r in rows))
    for start in range(0, max(1, len(body)), rows_per_slide):
        part = body[start:start + rows_per_slide]
        table_rows = [header] + part if body else [header]
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        _add_title(slide, title, theme)
        table_shape = slide.shapes.add_table(len(table_rows), cols, Inches(0.55), Inches(1.42), Inches(12.2), Inches(5.45))
        table = table_shape.table
        for ci in range(cols):
            table.columns[ci].width = Inches(12.2 / cols)
        for ri, row in enumerate(table_rows):
            for ci in range(cols):
                value = str(row[ci]) if ci < len(row) else ""
                cell = table.cell(ri, ci)
                cell.text = value
                cell.margin_left = Inches(0.06); cell.margin_right = Inches(0.06)
                cell.margin_top = Inches(0.035); cell.margin_bottom = Inches(0.035)
                tf = cell.text_frame; tf.word_wrap = True
                for p in tf.paragraphs:
                    p.font.size = Pt(10 if len(value) < 100 else 8.5)
                    p.font.bold = ri == 0
                    p.font.color.rgb = _rgb(theme.accent) if ri == 0 else RGBColor(35, 35, 35)
        _add_footer(slide, slide_number, theme)
        slide_number += 1
    return slide_number


def render_pptx(model, path, theme_name="professional", preserve_content=True):
    theme = get_theme(theme_name)
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    slide_number = 1
    _add_title_slide(prs, model, theme)
    slide_number += 1

    current_title = "Overview"
    items: list[_ContentItem] = []

    def flush():
        nonlocal items, slide_number
        if not items:
            return
        # A content slide has a bounded number of semantic items. Long prose was
        # already split into sentence/word-safe chunks, so no source text is lost.
        for start in range(0, len(items), 5):
            _add_content_slide(prs, current_title, items[start:start + 5], theme, slide_number)
            slide_number += 1
        items = []

    for block in model.blocks:
        if block.kind == "heading":
            level = block.level or 1
            if level == 1:
                flush()
                # H1 headings become visual section dividers. The source text is
                # still represented verbatim on the slide.
                slide = prs.slides.add_slide(prs.slide_layouts[6])
                _add_title(slide, block.text, theme, section=True)
                _add_footer(slide, slide_number, theme)
                slide_number += 1
                current_title = block.text
            elif level == 2:
                if items:
                    flush()
                current_title = block.text
            else:
                for chunk in _split_text(block.text, 220):
                    items.append(_ContentItem(chunk, level=1, kind="subheading"))
        elif block.kind in {"paragraph", "callout", "list_item"}:
            level = 1 if block.kind == "list_item" else 0
            for chunk in _split_text(block.text):
                items.append(_ContentItem(chunk, level=level, kind=block.kind))
                if len(items) >= 5:
                    flush()
        elif block.kind == "table" and block.rows:
            flush()
            slide_number = _add_table_slides(prs, current_title, block.rows, theme, slide_number)

    flush()
    prs.save(str(path))
    return path
