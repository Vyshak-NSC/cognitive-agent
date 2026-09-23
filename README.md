# Cognitive Persistence Agent 7.2
Version 7.2 is a modular, project-scoped agentic cognition system for software repositories, mixed work documents, research corpora, and long-form narrative projects.
## What it does
- Persistent entity-centric cognition: entities, attributes, temporal states, relationships, events, provenance, decisions, evidence, and changes.
- Local metadata index and deterministic retrieval before LLM reasoning. Normal retrieval does not require an embedding API call.
- Agentic tool execution with isolated workspace/file/document/cognition tools.
- Source/workspace separation so agent edits can be reviewed before they become authoritative.
- Draft, review, approval, re-ingestion, and VCS workflows.
- Code cognition and parsing, with Python AST support and tree-sitter compatibility for other languages.
- PDF, DOCX, PPTX, XLSX, XML, text/code, and binary artifact handling.
- Persistent project instructions, chat sessions, session memory, world model, impact analysis, and validation.
- Streamlit UI, terminal launcher, and FastAPI compatibility API.
- Project-scoped LLM provider configuration for Gemini, OpenAI, OpenRouter, Anthropic-compatible, and Azure-compatible paths exposed by the provider layer.
## Streamlit UI
The UI is located at:
`ragapp/interfaces/streamlit_app/main.py`
It contains project selection/creation, Chat, Files, Cognition, Review, Instructions, History, and Settings. The file manager supports source/workspace navigation and artifact operations.
## Windows quick start
1. Install Python 3.11+.
2. Extract this folder.
3. Put the required API key in `.env` or configure it under the project's Settings page.
4. Double-click `run_streamlit.bat`.
The first run creates `.venv` and installs dependencies. The browser UI normally appears at `http://localhost:8501`.
## Manual start
```powershell
cd C:\path\to\cognitive-persistence-agent-v7.2
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
python -m streamlit run ragapp\interfaces\streamlit_app\main.py
```
## Terminal launcher
```powershell
python -m ragapp
```
Choose Streamlit or terminal chat from the launcher.
## API
```powershell
python -m uvicorn api:app --reload
```
Health check: `http://127.0.0.1:8000/health`
## Project storage
Runtime projects are created under the configured projects root and are intentionally excluded from source control. Each project keeps authoritative cognition files separately from the SQLite metadata projection and workspace artifacts.
## Extension model
New features should be introduced behind an existing boundary whenever possible: extractor, cognition compiler, retrieval implementation, service/tool, provider adapter, or UI/API adapter. Existing callers should depend on stable interfaces and compatibility methods. Persistent schema changes should be migrated rather than silently changing existing records.

## Structure-aware document cognition

The cognition compiler uses a format-aware document pipeline for non-code source
artifacts. PDF, DOCX, PPTX, XLSX, HTML, Markdown and plain-text inputs are parsed
into a transient structural representation before semantic compilation.

The persistent `cognition/_index/document_index.json` contains **metadata only**:
- document/artifact identity and format
- page, paragraph, slide, sheet/cell and character locators
- chapter/scene/section hierarchy
- segment/node relationships
- entity/event/relationship source-location references

It does not store the document body. Source text is fetched later only when a
specific locator is requested.

The agent-facing cognition tools now support:
- `get_document_structure`
- `resolve_document_section`
- `get_section_cognition`
- `get_cognition_locations`
- `get_relationship_history`
- `read_source_location`

This lets a query such as “what happened in Chapter 62?” resolve the chapter
metadata first, obtain its associated entity/event/relationship IDs and page
range, and only then fetch the required source pages. The system therefore does
not need to scan the full source document for every question.

### Compilation architecture

```text
source artifact
    -> format-aware parser
    -> structural document graph
    -> metadata-only document index
    -> structure-aware segments
    -> LLM semantic extraction
    -> entity/event/relationship cognition
    -> exact source-location attachment
    -> temporal/state/relationship history
    -> local retrieval/navigation tools
```

Structural parsing is deterministic. The LLM is used for semantic interpretation,
not for inventing page numbers or other physical document locations.
