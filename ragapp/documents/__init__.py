"""Format-neutral document transformation and professional artifact rendering."""
from .model import DocumentModel, Block
from .parser import parse_docx
from .pdf_renderer import render_pdf
from .docx_renderer import render_docx
from .pptx_renderer import render_pptx
from .html_renderer import render_html
from .markdown_renderer import render_markdown
from .validator import validate_artifact, validate_pdf
__all__=['DocumentModel','Block','parse_docx','render_pdf','render_docx','render_pptx','render_html','render_markdown','validate_artifact','validate_pdf']
