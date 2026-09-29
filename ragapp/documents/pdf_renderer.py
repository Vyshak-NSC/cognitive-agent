"""Professional PDF renderer using ReportLab Platypus, not manual drawString layout."""
from __future__ import annotations
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    KeepTogether, PageBreak, ListFlowable, ListItem
)
from .themes import get_theme


def _styles(theme):
    base = getSampleStyleSheet()
    return {
        "body": ParagraphStyle("Body", parent=base["BodyText"], fontName=theme.body_font, fontSize=theme.body_size, leading=theme.body_leading, spaceAfter=theme.paragraph_space_after, textColor=colors.HexColor("#202428")),
        "title": ParagraphStyle("Title", parent=base["Title"], fontName=theme.heading_font, fontSize=theme.title_size, leading=theme.title_size * 1.15, alignment=TA_CENTER, spaceAfter=18, textColor=theme.accent),
        "h1": ParagraphStyle("H1", parent=base["Heading1"], fontName=theme.heading_font, fontSize=theme.h1_size, leading=theme.h1_size * 1.2, spaceBefore=15, spaceAfter=8, keepWithNext=True, textColor=theme.accent),
        "h2": ParagraphStyle("H2", parent=base["Heading2"], fontName=theme.heading_font, fontSize=theme.h2_size, leading=theme.h2_size * 1.25, spaceBefore=12, spaceAfter=6, keepWithNext=True, textColor=theme.accent),
        "h3": ParagraphStyle("H3", parent=base["Heading3"], fontName=theme.heading_font, fontSize=theme.h3_size, leading=theme.h3_size * 1.25, spaceBefore=9, spaceAfter=5, keepWithNext=True),
        "callout": ParagraphStyle("Callout", parent=base["BodyText"], fontName=theme.body_font, fontSize=theme.body_size, leading=theme.body_leading, leftIndent=10, rightIndent=8, borderWidth=.6, borderColor=theme.accent, borderPadding=8, backColor=theme.light_fill, spaceBefore=5, spaceAfter=9),
        "table": ParagraphStyle("TableText", parent=base["BodyText"], fontName=theme.body_font, fontSize=theme.table_size, leading=theme.table_size * 1.25),
        "table_head": ParagraphStyle("TableHead", parent=base["BodyText"], fontName=theme.heading_font, fontSize=theme.table_size, leading=theme.table_size * 1.25, textColor=colors.white),
    }


def _header_footer(canvas, doc, theme):
    canvas.saveState()
    width, height = theme.page_size
    canvas.setFont(theme.body_font, 8)
    canvas.setFillColor(colors.HexColor("#687078"))
    if theme.header_text:
        canvas.drawString(theme.margin_left, height - 30, theme.header_text)
    footer = theme.footer_text.format(page=doc.page)
    canvas.drawRightString(width - theme.margin_right, 28, footer)
    canvas.restoreState()


def render_pdf(model, output_path, theme_name="professional", include_toc=False):
    theme = get_theme(theme_name)
    styles = _styles(theme)
    width, height = theme.page_size
    frame = Frame(theme.margin_left, theme.margin_bottom, width-theme.margin_left-theme.margin_right, height-theme.margin_top-theme.margin_bottom, id="body")
    doc = BaseDocTemplate(str(output_path), pagesize=theme.page_size, leftMargin=theme.margin_left, rightMargin=theme.margin_right, topMargin=theme.margin_top, bottomMargin=theme.margin_bottom, title=model.title or "Document")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=lambda c, d: _header_footer(c, d, theme))])
    story = []
    first_content = True
    pending_list = []

    def flush_list():
        nonlocal pending_list
        if pending_list:
            items = [ListItem(Paragraph(escape(x.text), styles["body"])) for x in pending_list]
            story.append(ListFlowable(items, bulletType="1" if pending_list[0].ordered else "bullet", leftIndent=20))
            story.append(Spacer(1, 5))
            pending_list = []

    for block in model.blocks:
        if block.kind == "list_item":
            pending_list.append(block)
            continue
        flush_list()
        if block.kind == "heading":
            level = block.level or 1
            # First prominent heading is rendered as a title; all source text remains present.
            if first_content and level == 1:
                story.append(Paragraph(escape(block.text), styles["title"]))
            else:
                style = styles["h1"] if level == 1 else styles["h2"] if level == 2 else styles["h3"]
                story.append(Paragraph(escape(block.text), style))
            first_content = False
        elif block.kind == "paragraph":
            story.append(Paragraph(escape(block.text).replace("\n", "<br/>"), styles["body"]))
            first_content = False
        elif block.kind == "callout":
            story.append(Paragraph(escape(block.text).replace("\n", "<br/>"), styles["callout"]))
            first_content = False
        elif block.kind == "table" and block.rows:
            max_cols = max((len(r) for r in block.rows), default=1)
            usable = width-theme.margin_left-theme.margin_right
            col_widths = [usable/max_cols] * max_cols
            data = []
            for ri, row in enumerate(block.rows):
                style = styles["table_head"] if ri == 0 else styles["table"]
                padded = list(row) + [""] * (max_cols-len(row))
                data.append([Paragraph(escape(str(v)).replace("\n", "<br/>"), style) for v in padded])
            table = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), theme.accent),
                ("VALIGN", (0,0), (-1,-1), "TOP"),
                ("GRID", (0,0), (-1,-1), .35, colors.HexColor("#B8BEC4")),
                ("LEFTPADDING", (0,0), (-1,-1), 5), ("RIGHTPADDING", (0,0), (-1,-1), 5),
                ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, theme.light_fill]),
            ]))
            story.extend([table, Spacer(1, 9)])
            first_content = False
        elif block.kind == "page_break":
            story.append(PageBreak())
    flush_list()
    doc.build(story)
    return str(output_path)
