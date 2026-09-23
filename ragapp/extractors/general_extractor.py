from pathlib import Path
from ragapp.document_parser import parse_document


def extract_document(path: Path, area: str = "source"):
    """Return the canonical structured document representation."""
    return parse_document(path, area=area)
