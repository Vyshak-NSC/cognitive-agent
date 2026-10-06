# Codebase Guide

This guide explains the application as implemented in the inspected worktree. It is a maintainer-oriented map of runtime behavior, data flow, and tracked application files—not a guarantee that every capability mentioned in older documentation is currently operational.

## Contents

- [Scope and evidence](#scope-and-evidence)
- [Application overview](#application-overview)
- [Runtime and persistence layout](#runtime-and-persistence-layout)
- [End-to-end flows](#end-to-end-flows)
  - [Launch and authentication](#launch-and-authentication)
  - [Chat and agent execution](#chat-and-agent-execution)
  - [Project files, review, and history](#project-files-review-and-history)
  - [Cognition ingestion and retrieval](#cognition-ingestion-and-retrieval)
  - [Document conversion](#document-conversion)
- [LLM and tool boundaries](#llm-and-tool-boundaries)
- [Development and tests](#development-and-tests)
- [Tracked-file inventory](#tracked-file-inventory)
  - [Root files and package entrypoints](#root-files-and-package-entrypoints)
  - [Agent runtime](#agent-runtime)
  - [Authentication](#authentication)
  - [Chat sessions and project selection](#chat-sessions-and-project-selection)
  - [Cognition and ingestion](#cognition-and-ingestion)
  - [Core services](#core-services)
  - [Document conversion](#document-conversion-1)
  - [Extractors](#extractors)
  - [Interfaces](#interfaces)
  - [LLM integration](#llm-integration)
  - [Tools](#tools)
  - [Visualization and workspace helpers](#visualization-and-workspace-helpers)
  - [Tests](#tests)
  - [Generated and dependency metadata](#generated-and-dependency-metadata)
- [Caveats and unverified behavior](#caveats-and-unverified-behavior)
- [Coverage and validation](#coverage-and-validation)

## Scope and evidence

This guide uses the current source and current worktree state. It does not inspect `.env`, `.venv` or its third-party packages, or the contents of local runtime/binary artifacts such as `auth.db` and `cog.zip`. It describes `uv.lock` and egg-info metadata only by their general role.

There are **127 tracked paths**: **109 `.py` paths** and 18 other paths. The Python paths consist of 107 application paths (including one deleted in the worktree) and two tracked tests. Thus, 106 application Python files currently exist. The non-Python paths include top-level documentation/configuration, three batch scripts, the extensionless `entrypointpy` Streamlit launcher, the lockfile, and six egg-info metadata files. One additional test file, `tests/test_chat_cognition.py`, was added in the current worktree after this tracked-file inventory was first produced.

Pre-existing user changes have been preserved:

- Modified: `ragapp/agent/cognitive_cycle.py`, `ragapp/agent/loop.py`, `ragapp/cognition/compiler.py`, `ragapp/cognition/document_compiler.py`, `ragapp/cognition/merge.py`, and `ragapp/tools/compile_tools.py`.
- Deleted: `ragapp/cognition/classifier.py`. It is tracked by Git but is absent from the current worktree, so its old implementation is not reconstructed.

## Application overview

The application is a local project workspace with two user-facing surfaces: a Streamlit browser application and a separate terminal chat. Its central concepts are:

- **Source**: authoritative project inputs and approved files.
- **Workspace**: editable working files and proposals.
- **Cognition**: canonical JSON records about project files, symbols, concepts, knowledge, and relations.
- **Sessions/logs**: chat branch state and execution records.

The agent combines bounded request/context selection, a configured LLM provider, and a registry of explicitly exposed local tools. Compilation has deterministic source-code parsing and structure-aware document paths. Retrieval combines lexical metadata lookup, optional local semantic search, and bounded graph/context expansion. Review services mediate promotion of staged project-file changes. A separate document-conversion subsystem parses a document representation and renders output formats.

No current tracked `api.py` implements an HTTP API. The Uvicorn launcher and references in older documentation are not evidence of an operational API in this worktree.

## Runtime and persistence layout

### Project layout

`CognitionStore` places projects under `PROJECTS_ROOT` (default `./projects`) using normalized user/project identifiers. Initialization creates project areas similar to:

```text
<project>/
  source/       authoritative inputs and approved files
  workspace/    editable files, working copies, and proposals
  cognition/    canonical records, document index, and session memory
  sessions/     chat-session JSON and branch state
  log/          execution/history records
  .system/      project configuration and internal/derived state
```

Canonical cognition records are filesystem JSON organized by record kind, including entities, relationships, events, locations, concepts, definitions, and knowledge. SQLite metadata and semantic-index data are derived lookup structures, not the canonical record store. The document index stores document structure, provenance, and locators, rather than a full source body. However, the deterministic document-compilation path can persist segment text as canonical knowledge records.

Project configuration is stored below `.system/`. The document index is under the store's index area; optional local vector data is persisted separately from canonical JSON. The `ChatSessionStore` stores each session as JSON in the store's sessions area, with turns linked by parent IDs. `SessionMemory` is an append-only JSONL file under cognition's session-memory directory.

`VCSManager` versions the managed `source/`, `workspace/`, `cognition/`, and `log/` areas. It does not imply that every file below `.system/` or the sessions area is included in the same Git history. Older schema compatibility logic may rebuild incompatible cognition storage; check the actual migration path before assuming older canonical data is preserved in place.

### Runnable surfaces

- `python main.py` and `python -m ragapp` open the interactive launcher menu.
- The launcher can run the Streamlit script or terminal chat as a subprocess.
- Direct browser launch is `streamlit run ragapp/interfaces/streamlit_app/main.py`.
- `run.bat` is a Windows package/launcher convenience script; `run_streamlit.bat` and `run_api.bat` are additional Windows launch helpers.
- `run_api.bat` invokes Uvicorn with `api:app`, but that module is absent from the tracked/current source.

The Streamlit UI is a script that executes at module load, not a controller exposing `main()`. Its sidebar navigation in the current `main.py` includes Chat, Files, Cognition, Review, Instructions, History, and Settings.

## End-to-end flows

### Launch and authentication

The package launcher offers Streamlit or terminal chat. The Streamlit script initializes the local auth database, runs the login/registration gate, and then initializes project/session navigation. Authentication is implemented with a SQLite users table, PBKDF2-HMAC-SHA256 password hashes plus salts, JWT access tokens, and browser-cookie/session handling. User records also include an admin flag. This is local account handling, not evidence of an external identity-provider integration.

Runtime settings, including token expiry, hash iteration count, auth database path, project root, and agent limits, are read by `ragapp.settings`. The fallback JWT secret in code is a development placeholder; deployments should supply a secure configured value. `.env` was deliberately not read.

`execution.project` creates/selects/renames/deletes project stores and initializes project areas; project creation attempts Git initialization but can remain usable if Git is unavailable. The current terminal flow asks for a username/project and only continues if cognition is already initialized; its prompt directs users to the Streamlit UI to add artifacts and compile.

### Chat and agent execution

A chat turn follows this general flow:

1. The interface assembles an `AgentRunContext` with the transcript, tools, selected project/cognition store, and agent/policy context.
2. `ContextSelector` chooses relevant context and a bounded tool/capability set using request intent and project/agent signals. Local semantic retrieval may contribute routing/context hints.
3. `llm.provider` and `llm.registry` resolve a configured provider and model. Protocol-specific modules translate messages and tool schemas to/from provider wire formats.
4. `agent.loop` owns the bounded turn loop: send model context, dispatch requested tools through `ToolRegistry`, append tool results, and repeat until completion or the configured step limit.
5. Explicit cognition changes run as deterministic runtime operations before the model response. This includes chat-authored creation of characters/entities and related traits, conditions, rules, or concepts; no source/workspace file is required. Requests to generate/compile cognition pass the active user/assistant transcript as source text to the compiler using the same canonical JSON schema as document cognition (bounded by the configured context size). The schema output is mapped to entity, relationship, event, location, concept, definition, and knowledge persistence operations and saved through the cognition transaction. Chat-origin provenance is recorded without inventing document paths or locators. Empty or rejected plans fail rather than being treated as successful updates.
6. The loop applies project inspection/mutation contracts, handles structured `<STATE_UPDATE>` cognition changes, stages eligible file mutations as workspace drafts, and records tool/trace/session outcomes.
7. The UI renders the answer and persists chat turn state. `ChatSessionStore` maintains branch lineage rather than storing only one flat transcript.

The model is not given arbitrary direct filesystem access. It can request only registered tools; those tools enforce the project boundary and policy. `CognitiveCycle` provides lightweight lifecycle state/briefing helpers used by the loop; the actual tool/model orchestration and execution contracts remain in `agent.loop` and its collaborators.

### Project files, review, and history

The project-file service distinguishes authoritative Source from editable Workspace. Service-mediated agent changes are staged as workspace proposals with draft metadata rather than silently replacing authoritative Source. `ApprovalEngine` applies an approved draft to Source and can trigger selective reingestion. The Review UI calls this service for approve/reject actions.

`VCSManager` commits/checkpoints managed project areas and supports recovery through forward materialization instead of rewriting history. The Streamlit chat UI associates turn state with checkpoints. Branch switching selects a turn lineage and materializes its state as a new forward checkpoint; branch-aware tests verify sibling turns remain available.

There are also format-specific file tools that write directly into Workspace. Those paths do not all route through `ProjectFileService`, so do not assume every binary/document-format edit automatically creates the same proposal metadata or Git checkpoint as the service-mediated project file workflow.

### Cognition ingestion and retrieval

**Code ingestion**

1. `code_parser` recognizes source extensions and parses with Tree-sitter grammars.
2. It extracts normalized syntax symbols, imports, references, source ranges, and parse errors.
3. `code_compiler` converts this deterministic structure into cognition records and relationships.
4. The higher-level `compiler` coordinates file discovery, code/document dispatch, checkpoints, and store updates.

Recognized extensions include Python, JavaScript/TypeScript, Java, C/C++, C#, Go, Rust, Ruby, PHP, Swift, Kotlin, Scala, Lua, Dart, Elixir, Erlang, Haskell, shell, SQL, HTML, CSS, JSON, and YAML. An extension match still depends on the requested Tree-sitter grammar loading successfully.

**Document ingestion**

1. `document_parser` extracts nodes, hierarchy, locators, and bounded text segments from PDF, DOCX, PPTX, XLSX, HTML, and selected text/config/code-like formats.
2. `DocumentCognitionCompiler` stores structural metadata and can add semantic enrichment through its explicitly supported LLM dispatch, or deterministic segment knowledge when enrichment is disabled.
3. `DocumentIndex` stores document/node/segment identifiers, relationships, and locators such as page, slide, paragraph, line, row, or character ranges. Canonical cognition records can attach to those evidence locations.

Compilation is additive and uses a pre-state Git checkpoint. Reingestion after approval can target changed paths. Chat compilation uses the same canonical extraction schema and persistence model as document cognition; it supplies conversation text in place of document segments and records chat provenance instead of file locators. Source recompile results are only reported as successful when the compiler itself succeeds and no selected source files failed; canonical record files are compared before/after so the UI can distinguish a successful no-op from actual record creation/update. Document text passed to the semantic compiler is transient input, but deterministic segment knowledge can contain the text as noted above.

**Retrieval**

1. Query tools search compact lexical/structured metadata, and may use FastEmbed vectors when the local semantic index is available.
2. Candidate records are ranked and expanded through bounded graph relationships.
3. Retrieval hydrates compact cognition/document context with provenance and locators, then context selection applies limits before sending it to the model.
4. Focused document queries can resolve sections and source locators. Generic cognition retrieval does not automatically return an entire source body.

Canonical JSON is the source of truth; SQLite metadata accelerates lookup, and optional vectors provide an additional local semantic signal.

### Document conversion

The conversion subsystem uses a format-neutral `DocumentModel`/block IR. `documents.parser` reads DOCX input into headings, paragraphs, lists, callouts, tables, and breaks. Renderers generate DOCX, PDF, PPTX, HTML, or Markdown output with named themes. `validator` extracts output text and compares token coverage and heading retention against the source IR; it reports output metadata such as page/slide counts where applicable. PDF uses ReportLab, and PPTX uses presentation-native slides.

This rendering flow is separate from cognition ingestion: conversion creates an output artifact, while cognition compilation stores knowledge/structure and evidence locators.

## LLM and tool boundaries

Provider selection and shared request behavior live in `llm.provider`/`llm.registry`; `clients.py` is a backwards-compatible facade that lazily asks the provider module for the configured client. `llm.prompts` defines a short system invariant prompt. The `llm.tool_calling` package contains shared and provider-specific translation modules for OpenAI-compatible, Anthropic, Gemini, OpenRouter, and Azure forms. These files translate request/response formats; the agent loop still owns local dispatch.

Tools are assembled by `ragapp.tools.build_default_tools` and registered with `ToolRegistry`. Tool domains include agent definitions, cognition and compilation, project files, VCS, workflows, binary/text operations, and specific document formats. A model response requesting a tool is not itself a side effect: the registered local handler must execute the operation.

The document compiler's semantic-enrichment provider dispatch is narrower than chat provider support. Its current code explicitly handles Gemini, OpenRouter, and `"azure"`; do not assume every configured chat provider is wired for semantic document compilation.

## Development and tests

`pyproject.toml` declares Python `>=3.12,<3.13`, dependencies, package discovery, and console scripts. A typical setup/test sequence, assuming `uv` is installed, is:

```powershell
uv sync
uv run --with pytest pytest -q
```

The two tracked tests are:

- `tests/test_chat_branching.py`: sibling turn preservation and forward VCS materialization when changing chat branches.
- `tests/test_document_cognition.py`: PDF page/section locators, body text versus metadata behavior, focused metadata-first retrieval, and generic cognition retrieval boundaries.

Tests and application startup were not run while creating this documentation. LLM-backed paths may require configured provider credentials; secrets should not be committed.

## Tracked-file inventory

### Root files and package entrypoints

| File | Purpose and mechanics |
|---|---|
| `README.md` | User-facing project overview, installation/use guidance, and feature narrative. Some operational claims (especially API/entrypoint references) are stale relative to source; README also states a different version than package metadata. |
| `PROJECT DETAILS.md` | Product scope and feature/design notes; useful as intent context, not proof that each described behavior is wired up. |
| `WORKING.md` | Maintainer/refactoring notes and architecture map. It has stale references and should be checked against current code. |
| `.env.example` | Environment-variable template. Values are not reproduced here. The real `.env` file was not inspected. |
| `.gitignore` | Excludes local environments, generated caches/build output, and selected local runtime data from source control. |
| `main.py` | Thin root entrypoint that imports and runs `ragapp.interfaces.launcher.main`. |
| `pyproject.toml` | Python package metadata, dependency list, supported Python range, setuptools discovery, and CLI scripts. It declares `cognitive-agent` correctly via `interfaces.launcher`, but `cognitive-streamlit` points to `streamlit_app.main:main`, which is absent. |
| `python-version` | Version-manager hint for the intended Python runtime. |
| `run.bat` | Windows convenience entrypoint for the package/launcher workflow. |
| `run_api.bat` | Calls Uvicorn for `api:app`; the referenced `api.py` is absent, so this does not currently establish a usable API. |
| `run_streamlit.bat` | Windows convenience launcher for the Streamlit interface. |
| `ragapp/__init__.py` | Marks the package and resolves package version metadata for runtime imports. |
| `ragapp/__main__.py` | Enables `python -m ragapp` by delegating to `interfaces.launcher.main`. |
| `ragapp/config.py` | Creates/loads/saves project YAML configuration and resolves provider API keys/models from project settings and environment variables. |
| `ragapp/settings.py` | Reads application-wide environment configuration: auth database, project root, JWT/hash settings, model keys, and agent/context limits. |
| `ragapp/logging_config.py` | Idempotent root logging configuration; sets format/level and quiets selected noisy HTTP libraries. |
| `ragapp/clients.py` | Backwards-compatible lazy client facade. Avoids constructing a provider SDK client at import time; delegates to `llm.provider`. |
| `ragapp/core_ext.py` | Empty extension/compatibility module in the current worktree; it has no independent behavior. |
| `ragapp/document_parser.py` | Shared format-aware structural parser used by document cognition and narrative extraction; creates nodes/segments with locators while keeping a compact persistable representation. |
| `ragapp/chat_sessions.py` | JSON-backed branch-aware session store. Migrates flat legacy transcripts, stores turns by parent ID, derives active transcript from a lineage, and supports append/fork/branch switch/rename/delete. |
| `ragapp/interfaces/launcher.py` | Interactive menu that launches Streamlit or terminal chat as subprocesses. |
| `ragapp/interfaces/__init__.py` | Package marker for interface modules. |

### Agent runtime

| File | Purpose and mechanics |
|---|---|
| `ragapp/agent/__init__.py` | Package marker and small public agent API wrapper. |
| `ragapp/agent/cognitive_cycle.py` | Lightweight lifecycle-state/briefing helper used by the loop; not the primary execution orchestrator. Modified in the pre-existing worktree. |
| `ragapp/agent/context_selector.py` | Selects bounded request context, intent-specific tools, and relevant project/agent signals; can use semantic routing hints. |
| `ragapp/agent/loop.py` | Main LLM/tool turn loop, policy enforcement, project contract checks, state-update handling, draft creation, and trace/session recording. Modified in the pre-existing worktree. |
| `ragapp/agent/registry.py` | Registers tool definitions and dispatches calls through the available tool handlers. |
| `ragapp/agent/run_context.py` | Defines per-run transcript, project/store, agent, tool, and policy context passed among runtime components. |
| `ragapp/agent/trace.py` | Captures execution steps/tool calls and related trace details for inspection/logging. |

### Authentication

| File | Purpose and mechanics |
|---|---|
| `ragapp/auth/__init__.py` | Package marker for authentication components. |
| `ragapp/auth/db.py` | Opens the SQLite auth database and initializes/migrates the users table, including the `is_admin` column. |
| `ragapp/auth/passwords.py` | PBKDF2-HMAC-SHA256 password hashing with random salt and constant-time hash comparison. |
| `ragapp/auth/tokens.py` | Issues and verifies JWT access tokens using application token settings. |
| `ragapp/auth/users.py` | Normalizes usernames, creates users, checks admin status, and verifies login credentials against SQLite. |
| `ragapp/auth/cli.py` | Interactive local user-creation command; prompts for username/password/admin role and initializes auth storage. |

### Chat sessions and project selection

| File | Purpose and mechanics |
|---|---|
| `ragapp/execution/project.py` | Project CRUD and selection helpers. Creates initialized stores, attempts Git initialization, lists/renames/deletes projects, and resolves project workspace paths. |
| `ragapp/workspace/__init__.py` | Package marker for workspace helpers. |
| `ragapp/workspace/manager.py` | Maintains the current project via a `ContextVar`, resolves paths inside Source/Workspace, lists files, and provides copy/delete helpers. |
| `ragapp/interfaces/cli/__init__.py` | Package marker for terminal interface modules. |
| `ragapp/interfaces/cli/terminal.py` | Terminal chat loop; asks for username/project, requires an initialized cognition store, builds default tools/context, calls `run_agent`, and prints responses/tool and draft counts. |

### Cognition and ingestion

| File | Purpose and mechanics |
|---|---|
| `ragapp/cognition/__init__.py` | Package marker and cognition-facing exports. |
| `ragapp/cognition/classifier.py` | **Tracked but deleted in the current worktree.** Contents and prior behavior are unavailable and are not reconstructed. |
| `ragapp/cognition/code_parser.py` | Extension-based Tree-sitter parsing; normalizes source symbols, imports, references, source ranges, and errors. |
| `ragapp/cognition/code_compiler.py` | Maps parsed code structures to canonical cognition records and relationships. |
| `ragapp/cognition/compiler.py` | Coordinates project artifact discovery, code/document dispatch, pre-state checkpointing, and compilation/persistence. Modified in the pre-existing worktree. |
| `ragapp/cognition/document_compiler.py` | Converts parsed document nodes/segments into canonical cognition and document locators; optional LLM semantic enrichment or deterministic segment knowledge. Modified in the pre-existing worktree. |
| `ragapp/cognition/document_index.py` | JSON structure/provenance index for documents, nodes, segments, and cognition-record locators; intentionally excludes source node text. |
| `ragapp/cognition/merge.py` | Merges cognition state while handling identity/provenance/temporal semantics. Modified in the pre-existing worktree. |
| `ragapp/cognition/session_memory.py` | Append-only JSONL local session memory; distills transcript messages with hashes/turn provenance and can filter records by active branch lineage. |
| `ragapp/cognition/store.py` | Canonical project cognition store: creates project areas, manages schema/record files, provenance and temporal views, and coordinates metadata/snapshot helpers. |

### Core services

| File | Purpose and mechanics |
|---|---|
| `ragapp/core/__init__.py` | Package marker for core services. |
| `ragapp/core/agents.py` | Project-scoped JSON `AgentStore`; lists, gets, saves, enables/disables by record field, and deletes agent definitions without using an LLM. |
| `ragapp/core/approval.py` | Applies/rejects pending drafts and promotes approved changes, coordinating source update and follow-on reingestion/version behavior. |
| `ragapp/core/drafts.py` | Persists and lists staged proposal metadata used by review/approval flows. |
| `ragapp/core/instructions.py` | Stores and retrieves project/agent instruction text for context assembly. |
| `ragapp/core/metadata.py` | Defines and queries the compact SQLite metadata projection for lexical/structured cognition lookup. |
| `ragapp/core/metadata_sync.py` | Rebuilds/synchronizes metadata projections from canonical cognition/document state. |
| `ragapp/core/project_files.py` | Source/Workspace boundary service for file listing, reads, workspace writes, and staged source proposals. |
| `ragapp/core/reingest.py` | Selectively recompiles changed/approved source paths so cognition reflects current authoritative files. |
| `ragapp/core/retrieval.py` | Finds/ranks records and assembles bounded cognition/document context using metadata, semantic candidates, and graph expansion. |
| `ragapp/core/review.py` | Thin review facade over `DraftManager` and `ApprovalEngine` for listing, approving, and rejecting proposals. |
| `ragapp/core/semantic_index.py` | Optional FastEmbed vector indexing with SQLite persistence and NumPy cosine ranking. |
| `ragapp/core/vcs.py` | Git-backed checkpoints, managed-area commits, history/diffs, and forward-moving state restoration. |
| `ragapp/core/workflows.py` | Executes deterministic workflow prefixes (`set` and tool steps), resolves `$ref` values from earlier outputs, enforces allowed tools, and stops at explicit inference steps for later LLM work. |

### Document conversion

| File | Purpose and mechanics |
|---|---|
| `ragapp/documents/__init__.py` | Package marker/exports for document conversion. |
| `ragapp/documents/model.py` | Defines the conversion IR (`DocumentModel` and typed `Block`s), source-text aggregation, and per-kind counts. |
| `ragapp/documents/parser.py` | Parses DOCX into the conversion IR while preserving headings, paragraphs, tables, lists, callouts, and breaks. |
| `ragapp/documents/themes.py` | Defines named page, font, spacing, color, and header/footer presets used by renderers. |
| `ragapp/documents/docx_renderer.py` | Writes IR blocks as a DOCX using themed margins/styles, lists, tables, and page breaks. |
| `ragapp/documents/html_renderer.py` | Writes IR headings, paragraphs, callouts, lists, and tables as escaped HTML with print-aware CSS. |
| `ragapp/documents/markdown_renderer.py` | Writes IR blocks as Markdown headings, paragraphs, quote callouts, lists, and tables. |
| `ragapp/documents/pdf_renderer.py` | Writes PDF with ReportLab Platypus, styled paragraphs, lists, tables, page breaks, and headers/footers. |
| `ragapp/documents/pptx_renderer.py` | Creates presentation-native slides from IR; splits long text and multi-slide tables to retain content. |
| `ragapp/documents/validator.py` | Extracts text from supported rendered formats and checks token coverage/headings against source IR; reports issues and output page/slide/section metadata. |

### Extractors

| File | Purpose and mechanics |
|---|---|
| `ragapp/extractors/base.py` | Defines the `Extractor` protocol with the shared `extract(source)` interface. |
| `ragapp/extractors/code_extractor.py` | Convenience code extraction path with Python-specific parsing and dispatch for recognized extensions. |
| `ragapp/extractors/general_extractor.py` | Convenience document dispatch through the structural parser and summary of parsed structure. |
| `ragapp/extractors/narrative_extractor.py` | Produces structure-aware narrative segments with chapter/scene IDs, node IDs, locators, and transient text. |

### Interfaces

| File | Purpose and mechanics |
|---|---|
| `ragapp/interfaces/streamlit_app/__init__.py` | Package marker for Streamlit UI helpers. |
| `ragapp/interfaces/streamlit_app/main.py` | Top-level Streamlit app script: initializes authentication/UI state, project/session selection, navigation, chat, file management, cognition compilation/viewing, review, instructions, history, and settings. |
| `ragapp/interfaces/streamlit_app/auth_ui.py` | Login/logout/registration UI and authenticated session/cookie integration. |
| `ragapp/interfaces/streamlit_app/file_manager.py` | Source/Workspace browsing, upload/edit/create/delete/compile controls, and file-manager UI behavior. |
| `ragapp/interfaces/streamlit_app/previews.py` | Selects/renders supported file previews, including document-oriented output. |
| `ragapp/interfaces/streamlit_app/mermaid.py` | UI-independent Mermaid fence extraction, Unicode/label normalization, structural checks, and safe diagnostic fallback for invalid diagrams. |
| `ragapp/interfaces/streamlit_app/entrypointpy` | Extensionless tracked helper with a `main()` that launches Streamlit with the sibling `main.py`; not a conventional `.py` import module. |

### LLM integration

| File | Purpose and mechanics |
|---|---|
| `ragapp/llm/__init__.py` | Package marker for model/provider integration. |
| `ragapp/llm/prompts.py` | Short invariant system prompt: use tools for operations, do not invent state, and do not claim persistence without execution evidence. |
| `ragapp/llm/provider.py` | Shared configured provider facade and provider resolution/request interface. |
| `ragapp/llm/registry.py` | Provider specification/lookup and shared request limiting/throttling entrypoints. |
| `ragapp/llm/tool_calling/__init__.py` | Package marker for provider tool-call protocol translation. |
| `ragapp/llm/tool_calling/common.py` | Shared internal message/tool-call representations and translation helpers. |
| `ragapp/llm/tool_calling/openai.py` | OpenAI-style tool-call conversion to/from internal representations. |
| `ragapp/llm/tool_calling/openai_wire.py` | OpenAI-compatible wire-format message/tool-schema handling. |
| `ragapp/llm/tool_calling/anthropic.py` | Anthropic tool-use conversion to/from internal representations. |
| `ragapp/llm/tool_calling/anthropic_wire.py` | Anthropic-specific wire-format messages/content blocks and tool schemas. |
| `ragapp/llm/tool_calling/gemini.py` | Gemini function-call/request/response translation. |
| `ragapp/llm/tool_calling/openrouter.py` | OpenRouter request/response tool-call translation. |
| `ragapp/llm/tool_calling/azure_openai.py` | Azure OpenAI tool-call protocol variant. |
| `ragapp/llm/tool_calling/azure_anthropic.py` | Azure Anthropic tool-call protocol variant. |
| `ragapp/llm/tool_calling/azure_foundry.py` | Azure AI Foundry tool-call protocol handling. |

### Tools

| File | Purpose and mechanics |
|---|---|
| `ragapp/tools/__init__.py` | Builds and exports the default tool collection for an authenticated user/project. |
| `ragapp/tools/definitions.py` | Common tool definition/schema/handler representation used by registry dispatch. |
| `ragapp/tools/agent_tools.py` | Exposes project-scoped agent definition operations through local tools. |
| `ragapp/tools/binary_files.py` | Reads/writes binary workspace content using encoded payloads. |
| `ragapp/tools/cognition_tools.py` | Exposes cognition query/context, record, compile, validation, and related operations to the agent. |
| `ragapp/tools/compile_tools.py` | Exposes explicit source/document compilation and recompilation operations. Modified in the pre-existing worktree. |
| `ragapp/tools/document_tools.py` | High-level document conversion/render/validation actions over the document IR. |
| `ragapp/tools/docx_files.py` | DOCX-specific inspection and workspace file actions. |
| `ragapp/tools/extractors.py` | Exposes extraction convenience functions as registered tools. |
| `ragapp/tools/pdf_files.py` | PDF-specific inspection and workspace read/write/edit/copy/delete actions. |
| `ragapp/tools/pptx_files.py` | Presentation-specific inspection and workspace file actions. |
| `ragapp/tools/project_files.py` | Agent-facing project listing/reading and text mutation; service-mediated actions use project-file policy boundaries. |
| `ragapp/tools/vcs_tools.py` | Exposes managed project history/checkpoint/diff/recovery operations. |
| `ragapp/tools/workflow_tools.py` | Exposes deterministic workflow execution and related tool operations. |
| `ragapp/tools/xlsx_files.py` | Spreadsheet/workbook and cell-oriented workspace operations. |
| `ragapp/tools/xml_files.py` | XML inspection and XPath-oriented query/edit operations. |

### Visualization and workspace helpers

| File | Purpose and mechanics |
|---|---|
| `ragapp/visualization/__init__.py` | Package marker for visualization helpers. |
| `ragapp/visualization/mermaid.py` | Mermaid rendering integration used by the UI after diagram text has been normalized/validated. |

### Tests

| File | Purpose and mechanics |
|---|---|
| `tests/test_chat_branching.py` | Checks sibling chat-turn preservation and VCS forward materialization when switching branches. |
| `tests/test_document_cognition.py` | Checks document locators and retrieval/body-text boundaries, including focused document metadata retrieval. |
| `tests/test_chat_cognition.py` | Checks chat-only entity/condition creation, explicit cognition intent routing, rejection of empty mutation plans, and truthful source-compile failure results. |

### Generated and dependency metadata

| File | Purpose |
|---|---|
| `uv.lock` | Pins the resolved dependency graph for repeatable setup; dependency metadata, not application logic. |
| `cognitive_persistence_agent.egg-info/PKG-INFO` | Generated package metadata; not application logic. |
| `cognitive_persistence_agent.egg-info/SOURCES.txt` | Generated package source-file manifest; not application logic. |
| `cognitive_persistence_agent.egg-info/dependency_links.txt` | Generated packaging metadata; not application logic. |
| `cognitive_persistence_agent.egg-info/entry_points.txt` | Generated packaging entry-point metadata; not application logic. |
| `cognitive_persistence_agent.egg-info/requires.txt` | Generated dependency metadata; not application logic. |
| `cognitive_persistence_agent.egg-info/top_level.txt` | Generated package-name metadata; not application logic. |

Local virtual environments, caches, databases, and binary archives are runtime/generated artifacts and are not inventoried as application source.

## Caveats and unverified behavior

- **HTTP API:** `run_api.bat` and documentation mention an API, but the current tracked worktree has no `api.py`; no routes or running API are documented here.
- **Streamlit console script:** `pyproject.toml` points `cognitive-streamlit` to `ragapp.interfaces.streamlit_app.main:main`, but `main.py` is a top-level Streamlit script without that function. The separate `entrypointpy` helper is not an importable module with the declared name.
- **Documentation drift:** Existing Markdown documents include intended or older descriptions that disagree with current code. Source behavior takes precedence.
- **Version drift:** `pyproject.toml` declares `9.5.5`; README reports `9.5.3`.
- **Deleted classifier:** The tracked `ragapp/cognition/classifier.py` is missing from the worktree; prior logic cannot be described from current evidence.
- **Document-body nuance:** The document index omits node text, but deterministic document compilation can store segment text as canonical knowledge.
- **Provider nuance:** General chat provider support is broader than document compiler semantic-enrichment dispatch.
- **Write/version nuance:** Some format-specific tools write directly to Workspace instead of routing through project-file proposal/checkpoint services. Inspect the selected tool path before assuming approval/versioning behavior.
- **Validation scope:** Focused chat-cognition tests pass. Full unittest discovery also encounters failures in two document cognition tests: one calls a missing `CognitionStore.retrieve` method and another expects metadata candidates that are not returned. No app startup or live provider call was performed.

## Coverage and validation

Coverage is **127/127 tracked paths** from the original inventory: 109 Python paths (107 application paths including the deleted classifier, plus two tracked tests), 106 currently present application Python files, and 18 non-Python tracked paths, including the extensionless launcher and generated/dependency metadata. `tests/test_chat_cognition.py` is one additional untracked test file in the current worktree and is inventoried above.

The original tracked inventory and current worktree state were checked after authoring. The chat-cognition test file and implementation changes are additions to this worktree; pre-existing source edits/deletion were preserved. Focused tests were run; full-suite results and remaining document-test failures are noted above.
