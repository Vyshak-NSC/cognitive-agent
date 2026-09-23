"""PDF tools. Reading extracts text; structural PDF operations preserve original pages."""
import io
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from ragapp.tools.definitions import Tool
from ragapp.workspace.manager import resolve_workspace_path


def _path(username, p): return resolve_workspace_path(username, p)

def read_pdf(username, relative_path, start_page=None, end_page=None):
    path = _path(username, relative_path)
    if not path.is_file(): raise FileNotFoundError(f"File not found: {relative_path}")
    reader = PdfReader(str(path)); total = len(reader.pages)
    start = 1 if start_page is None else start_page; end = total if end_page is None else min(end_page, total)
    if start < 1 or end < start or start > total: raise ValueError("Invalid PDF page range")
    return {"path": relative_path, "page_count": total, "pages": [{"page": i, "text": reader.pages[i-1].extract_text() or ""} for i in range(start, end+1)]}

def write_pdf(username, relative_path, pages):
    path = _path(username, relative_path); path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists(): raise FileExistsError(f"File already exists: {relative_path}")
    writer = PdfWriter()
    for spec in pages:
        buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=(float(spec.get("width",612)), float(spec.get("height",792))))
        for line in spec.get("text", "").splitlines():
            c.drawString(float(spec.get("x",54)), float(spec.get("y",738)), line); spec["y"] = float(spec.get("y",738)) - float(spec.get("line_height",14))
        c.showPage(); c.save(); buf.seek(0); writer.add_page(PdfReader(buf).pages[0])
    with path.open("wb") as f: writer.write(f)
    return {"path": relative_path, "status": "created"}

def copy_pdf_pages(username, source_relative_path, destination_relative_path, start_page, end_page):
    source = _path(username, source_relative_path); dest = _path(username, destination_relative_path)
    if not source.is_file(): raise FileNotFoundError(f"Source PDF not found: {source_relative_path}")
    if source.suffix.lower() != ".pdf": raise ValueError("Source must be a PDF")
    if dest.exists(): raise FileExistsError(f"Destination already exists: {destination_relative_path}")
    reader = PdfReader(str(source)); total = len(reader.pages)
    if start_page < 1 or end_page < start_page or end_page > total: raise ValueError("Invalid PDF page range")
    writer = PdfWriter(); [writer.add_page(reader.pages[i-1]) for i in range(start_page, end_page+1)]
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f: writer.write(f)
    return {"path": destination_relative_path, "status": "created", "pages": end_page-start_page+1}

def edit_pdf(username, relative_path, operation, page=None, text=None, x=54, y=738):
    path = _path(username, relative_path)
    if not path.is_file(): raise FileNotFoundError(f"File not found: {relative_path}")
    reader = PdfReader(str(path)); writer = PdfWriter()
    if operation == "delete_page":
        if page is None or page < 1 or page > len(reader.pages): raise ValueError("Invalid page")
        for i, p in enumerate(reader.pages, 1):
            if i != page: writer.add_page(p)
    elif operation == "add_text":
        if page is None or text is None or page < 1 or page > len(reader.pages): raise ValueError("add_text requires a valid page and text")
        for i, original in enumerate(reader.pages, 1):
            if i == page:
                w, h = float(original.mediabox.width), float(original.mediabox.height); buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=(w,h)); c.drawString(float(x), float(y), text); c.save(); buf.seek(0); overlay = PdfReader(buf).pages[0]; original.merge_page(overlay)
            writer.add_page(original)
    else: raise ValueError("PDF edit operation must be delete_page or add_text")
    with path.open("wb") as f: writer.write(f)
    return {"path": relative_path, "status": "edited"}

def delete_pdf(username, relative_path):
    path = _path(username, relative_path)
    if not path.is_file(): raise FileNotFoundError(f"File not found: {relative_path}")
    path.unlink(); return {"path": relative_path, "status": "deleted"}

def build_pdf_tools(username):
    return [
        Tool("read_pdf", "Read PDF text and page structure. Use start_page/end_page for specific pages.", {"type":"object","properties":{"relative_path":{"type":"string"},"start_page":{"type":"integer"},"end_page":{"type":"integer"}},"required":["relative_path"]}, lambda relative_path, start_page=None, end_page=None: read_pdf(username, relative_path, start_page, end_page)),
        Tool(
            "write_pdf",
            "Create a real PDF from page specifications. Use when creating a new PDF.",
            {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string"},
                    "pages": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string", "description": "Page body text. Use \\n for line breaks."},
                                "x": {"type": "number", "description": "Left margin in points. Default 54."},
                                "y": {"type": "number", "description": "Top starting baseline in points. Default 738."},
                                "width": {"type": "number", "description": "Page width in points. Default 612 (US Letter)."},
                                "height": {"type": "number", "description": "Page height in points. Default 792 (US Letter)."},
                                "line_height": {"type": "number", "description": "Vertical spacing between lines in points. Default 14."},
                            },
                            "required": ["text"],
                        },
                    },
                },
                "required": ["relative_path", "pages"],
            },
            lambda relative_path, pages: write_pdf(username, relative_path, pages),
        ),
        Tool("edit_pdf", "Perform structural PDF edits while preserving existing page content. Supports deleting a page or adding text as an overlay.", {"type":"object","properties":{"relative_path":{"type":"string"},"operation":{"type":"string","enum":["delete_page","add_text"]},"page":{"type":"integer"},"text":{"type":"string"},"x":{"type":"number"},"y":{"type":"number"}},"required":["relative_path","operation"]}, lambda relative_path, operation, page=None, text=None, x=54, y=738: edit_pdf(username, relative_path, operation, page, text, x, y)),
        Tool("copy_pdf_pages", "Copy a page range from one PDF to another without flattening or re-rendering the pages; original PDF page formatting is preserved.", {"type":"object","properties":{"source_relative_path":{"type":"string"},"destination_relative_path":{"type":"string"},"start_page":{"type":"integer"},"end_page":{"type":"integer"}},"required":["source_relative_path","destination_relative_path","start_page","end_page"]}, lambda source_relative_path, destination_relative_path, start_page, end_page: copy_pdf_pages(username, source_relative_path, destination_relative_path, start_page, end_page)),
        Tool("delete_pdf", "Delete an existing PDF from the AI workspace.", {"type":"object","properties":{"relative_path":{"type":"string"}},"required":["relative_path"]}, lambda relative_path: delete_pdf(username, relative_path)),
    ]
