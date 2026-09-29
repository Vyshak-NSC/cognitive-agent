"""High-level document transformation tools for professional artifacts."""
from __future__ import annotations
from ragapp.tools.definitions import Tool
from ragapp.workspace.manager import resolve_workspace_path, resolve_source_path
from ragapp.documents import parse_docx, render_pdf, render_docx, render_pptx, render_html, render_markdown, validate_artifact

_RENDERERS={'.pdf':render_pdf,'.docx':render_docx,'.pptx':render_pptx,'.html':render_html,'.htm':render_html,'.md':render_markdown,'.markdown':render_markdown}

def transform_document(username,source_relative_path,destination_relative_path,source_area='source',theme='professional',preserve_content=True):
    area=(source_area or 'source').strip().lower()
    source=resolve_source_path(username,source_relative_path) if area=='source' else resolve_workspace_path(username,source_relative_path) if area=='workspace' else None
    if source is None: raise ValueError("source_area must be 'source' or 'workspace'")
    if not source.is_file(): raise FileNotFoundError(f'File not found: {area}/{source_relative_path}')
    if source.suffix.lower()!='.docx': raise ValueError('Structured transformation currently accepts DOCX sources')
    dest=resolve_workspace_path(username,destination_relative_path); suffix=dest.suffix.lower()
    if suffix not in _RENDERERS: raise ValueError('Destination must be PDF, DOCX, PPTX, HTML, or Markdown')
    if dest.exists(): raise FileExistsError(f'File already exists: {destination_relative_path}')
    dest.parent.mkdir(parents=True,exist_ok=True); model=parse_docx(source); renderer=_RENDERERS[suffix]
    if suffix=='.pptx': renderer(model,dest,theme_name=theme,preserve_content=preserve_content)
    else: renderer(model,dest,theme_name=theme)
    # A presentation is a different medium, but preserve_content=True still requires source coverage.
    threshold=.90 if suffix=='.pptx' else .94
    qa=validate_artifact(model,dest,min_token_coverage=threshold if preserve_content else 0.0)
    if preserve_content and not qa['ok']:
        dest.unlink(missing_ok=True); raise RuntimeError('Rendered artifact failed preservation QA: '+'; '.join(qa['issues']))
    return {'path':destination_relative_path,'status':'created','mode':'transform','format':suffix.lstrip('.'),'theme':theme,'preserve_content':bool(preserve_content),'qa':qa,'source_counts':model.counts()}

def build_document_tools(username):
    return [Tool('transform_document',
      'Transform an existing DOCX into a professionally styled PDF, DOCX, PPTX, HTML, or Markdown artifact. Formatting/restyling preserves source content by default. PPTX uses slide-aware layouts and chunks long content instead of silently dropping it. Use preserve_content=false only when the user explicitly requests summarization or condensation.',
      {'type':'object','properties':{
        'source_relative_path':{'type':'string'},'destination_relative_path':{'type':'string'},'source_area':{'type':'string','enum':['source','workspace']},
        'theme':{'type':'string','enum':['professional','minimal','academic','report','fantasy_codex']},
        'preserve_content':{'type':'boolean','description':'Keep true for format/convert/restyle requests; false only for explicit summarization/condensation.'}},
       'required':['source_relative_path','destination_relative_path']},
      lambda source_relative_path,destination_relative_path,source_area='source',theme='professional',preserve_content=True: transform_document(username,source_relative_path,destination_relative_path,source_area,theme,preserve_content))]
