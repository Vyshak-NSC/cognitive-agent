"""Deterministic publication themes for generated documents."""
from __future__ import annotations
from dataclasses import dataclass
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib import colors

@dataclass(frozen=True)
class DocumentTheme:
    name: str
    page_size: tuple = A4
    margin_left: float = 52
    margin_right: float = 52
    margin_top: float = 58
    margin_bottom: float = 54
    body_font: str = "Helvetica"
    heading_font: str = "Helvetica-Bold"
    body_size: float = 10.2
    body_leading: float = 14.2
    title_size: float = 25
    h1_size: float = 18
    h2_size: float = 14
    h3_size: float = 11.5
    paragraph_space_after: float = 7
    table_size: float = 8.3
    header_text: str = ""
    footer_text: str = "{page}"
    accent = colors.HexColor("#263746")
    light_fill = colors.HexColor("#F1F3F5")

THEMES = {
    "professional": DocumentTheme("professional"),
    "minimal": DocumentTheme("minimal", margin_left=60, margin_right=60, body_size=10.5, body_leading=15),
    "academic": DocumentTheme("academic", page_size=LETTER, body_font="Times-Roman", heading_font="Times-Bold", body_size=10.5, body_leading=14.5),
    "report": DocumentTheme("report", header_text="REPORT"),
    "fantasy_codex": DocumentTheme("fantasy_codex", margin_left=56, margin_right=56, title_size=27, h1_size=19, h2_size=14.5, body_size=10.3, body_leading=14.7, header_text="THE MULTIVERSE CODEX"),
}

def get_theme(name: str | None) -> DocumentTheme:
    key = (name or "professional").strip().lower()
    if key not in THEMES:
        raise ValueError(f"Unknown document theme: {name}. Choose from: {', '.join(sorted(THEMES))}")
    return THEMES[key]
