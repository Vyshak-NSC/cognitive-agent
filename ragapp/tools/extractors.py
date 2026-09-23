"""Compatibility extractors.

Cognition compilation no longer flattens structured documents through this
module. Use ``extract_document_structure`` for format-aware parsing. The
legacy ``extract_text`` function remains for simple inspection callers.
"""
from pathlib import Path


def extract_document_structure(path: Path, area: str = "source", max_chars: int = 14000):
    from ragapp.document_parser import parse_document
    return parse_document(path, area=area, max_chars=max_chars)


def extract_text(path: Path) -> str:
    """Legacy flattened text view; not used by the cognition compiler."""
    try:
        doc = extract_document_structure(path, area="source", max_chars=10**9)
    except ValueError:
        return f"Binary artifact: {path.name}; inspect it with the artifact tools."
    pieces = []
    for segment in doc.segments:
        if segment.text:
            pieces.append(segment.text)
    if pieces:
        # Strip evidence node markers for legacy callers.
        return "\n\n".join(
            "\n".join(
                line.split("] ", 1)[1] if line.startswith("[") and "] " in line else line
                for line in piece.splitlines()
            )
            for piece in pieces
        )
    return f"Binary artifact: {path.name}; inspect it with the artifact tools."
