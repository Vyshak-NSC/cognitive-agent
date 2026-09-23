from pathlib import Path
from ragapp.document_parser import parse_document


def chunk_text(path: Path, max_chars=18000):
    """Structure-aware narrative segments.

    Backwards-compatible keys are retained while adding chapter/scene IDs and
    physical source locators. Segment text is transient and is not persisted by
    the document metadata index.
    """
    doc = parse_document(path, area="source", max_chars=max_chars)
    out=[]
    for seg in doc.segments:
        out.append({
            "id": seg.id,
            "chapter": doc.nodes.get(seg.chapter_id).title if seg.chapter_id and doc.nodes.get(seg.chapter_id) else None,
            "chapter_id": seg.chapter_id,
            "scene_id": seg.scene_id,
            "text": seg.text,
            "locator": seg.locator,
            "node_ids": seg.node_ids,
        })
    return out
