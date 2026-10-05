# Codebase Working Map: Pre-Analysis Guide

## Why This File Exists

This is a learning and pre-analysis map for the planned module-level and code-level review. It groups the repository into functional modules, identifies the responsibility of each Python file, and explains how data and control move between modules. It is not a refactor plan with approved deletions: apparent dead code must be checked against dynamic imports, public entry points, tests, external consumers, and intended product behavior before removal.

Read this alongside [PROJECT DETAILS.md](PROJECT%20DETAILS.md), which describes product behavior and example user scenarios. This file concentrates on source ownership, inter-module boundaries, and how to investigate the implementation safely.

## System At A Glance

```text
Streamlit UI ---------+
Terminal chat --------+--> AgentRunContext --> ContextSelector --> agent loop
FastAPI API ----------+                              |                 |
                                                     |                 +--> LLM provider adapters
                                                     |                 +--> registered tool registry
                                                     |                           |
                                                     +--> cognition/retrieval    +--> project services
                                                                                       |
                                             project store <---------------------------+
                                                  |       |        |        |
                                               source workspace cognition sessions
                                                  |       |        |        |
                                                  +------- Git/VCS -------+
```

The diagram is conceptual. Not every frontend exposes every operation, and some services are called directly by the UI/API rather than through the agent tool registry.

## Module 1: Application Entry, Configuration, and Project Lifecycle

**Purpose:** Start the application, load runtime configuration, establish project stores, and resolve paths without mixing project data with application code.

| File | Responsibility |
| --- | --- |
| `main.py` | Root launcher entry point; invokes `ragapp.interfaces.launcher.main`. |
| `api.py` | Builds the FastAPI app and exposes chat, projects, drafts, instructions, history, ingestion, cognition, sessions, file operations, checkpoints, and agent CRUD. |
| `ragapp/__init__.py` | Resolves the installed package version, with a development fallback. |
| `ragapp/__main__.py` | Supports `python -m ragapp` by starting the launcher. |
| `ragapp/config.py` | Reads/writes project YAML settings and resolves model names, API keys, and provider configuration. |
| `ragapp/settings.py` | Loads environment-backed defaults and runtime/storage limits. |
| `ragapp/logging_config.py` | Configures application logging. |
| `ragapp/execution/project.py` | Creates, discovers, renames, deletes, and opens per-user project stores. |
| `ragapp/workspace/manager.py` | Tracks the current project and resolves Source/Workspace paths with containment checks. |
| `ragapp/workspace/__init__.py` | Package marker. |
| `ragapp/clients.py` | Client-construction compatibility helper; no in-repository caller was found during static search. Verify intended compatibility before removing. |
| `ragapp/core_ext.py` | Empty extension placeholder in the current checkout; verify whether a plugin/import contract expects it. |

**Learning path:** Start with `main.py`, `ragapp/interfaces/launcher.py`, `ragapp/execution/project.py`, and `ragapp/workspace/manager.py`; then compare how the UI and API construct/open a project.

## Module 2: User Authentication

**Purpose:** Create local user accounts, verify credentials, and support browser login for the Streamlit experience.

| File | Responsibility |
| --- | --- |
| `ragapp/auth/__init__.py` | Authentication package exports. |
| `ragapp/auth/db.py` | Initializes and connects to the local SQLite user database. |
| `ragapp/auth/passwords.py` | Password hashing and verification helpers. |
| `ragapp/auth/users.py` | Creates users, authenticates them, normalizes usernames, and reads admin status. |
| `ragapp/auth/tokens.py` | Issues/verifies JWTs. |
| `ragapp/auth/cli.py` | Interactive command-line account creation. |
| `ragapp/interfaces/streamlit_app/auth_ui.py` | Streamlit login/registration UI and browser-cookie token handling. |

**Boundary to verify:** `api.py` currently defines routes without visible authentication dependencies. Streamlit authentication must not be assumed to protect the API. Review token issuance, validation, cookie handling, and route authorization as a separate security task before network exposure.

## Module 3: Agent Runtime and Turn Orchestration

**Purpose:** Convert one user turn into bounded context, an LLM request, zero or more tool calls, durable state changes, and a user-facing response.

| File | Responsibility |
| --- | --- |
| `ragapp/agent/__init__.py` | Package exports/lazy runner access. |
| `ragapp/agent/run_context.py` | Validates one run and resolves a selected custom agent; filters tools by allow/deny policy and builds agent guidance. |
| `ragapp/agent/context_selector.py` | Detects request intent and selects capabilities/context such as tools, cognition, instructions, and prompt modules. |
| `ragapp/agent/registry.py` | Converts registered `Tool` objects into provider schemas and dispatches tool calls. |
| `ragapp/agent/loop.py` | Main model/tool iteration; applies project mutation/review behavior, consumes cognition updates, records calls, and manages turn effects. |
| `ragapp/agent/cognitive_cycle.py` | Tracks cognitive-cycle lifecycle state during execution; `briefing()` had no caller found in static search. |
| `ragapp/agent/trace.py` | Emits bounded execution/provider/tool trace events. |
| `ragapp/agent/router.py` | Separate deterministic capability router (`route_request`, `allowed_tool_names`); no in-repository call sites found. Compare with `ContextSelector` before deciding whether to integrate or retire it. |
| `ragapp/llm/prompts.py` | Invariant system prompt for the agent runtime. |

**Primary flow:** A UI/API caller builds tools and an `AgentRunContext`; the context validates agent selection and filters tools; `ContextSelector` prepares turn-specific capabilities; `loop.py` calls the provider; `ToolRegistry` invokes tools; the loop records state updates, drafts, and traces; the caller persists the transcript/session metadata.

**Important distinction:** `ragapp/agent/router.py` is not the same as the active context selector. The latter is imported by the loop; the former has no in-repository call site found so far.

## Module 4: LLM Provider Adapters

**Purpose:** Keep provider selection, request throttling, credentials, model selection, and tool-call wire formats behind a common runtime interface.

| File | Responsibility |
| --- | --- |
| `ragapp/llm/__init__.py` | Package marker. |
| `ragapp/llm/registry.py` | Provider IDs, labels, adapter names, key slots, environment settings, model defaults, and rate-limit defaults. |
| `ragapp/llm/provider.py` | Validates configured provider, imports the adapter, throttles calls, and records request/response traces. |
| `ragapp/llm/tool_calling/__init__.py` | Provider-neutral message/tool-call dispatch helpers. |
| `ragapp/llm/tool_calling/common.py` | Shared credentials, client caching, message normalization, and model configuration helpers. |
| `ragapp/llm/tool_calling/gemini.py` | Gemini API adapter. |
| `ragapp/llm/tool_calling/openai.py` | OpenAI API adapter. |
| `ragapp/llm/tool_calling/openai_wire.py` | Shared OpenAI-compatible request/response and tool-call wire handling. |
| `ragapp/llm/tool_calling/openrouter.py` | OpenRouter adapter, including its fallback/retry behavior. |
| `ragapp/llm/tool_calling/anthropic.py` | Anthropic API adapter. |
| `ragapp/llm/tool_calling/anthropic_wire.py` | Anthropic message/tool-call wire handling. |
| `ragapp/llm/tool_calling/azure_openai.py` | Azure OpenAI / Foundry adapter. |
| `ragapp/llm/tool_calling/azure_anthropic.py` | Azure Anthropic / Foundry adapter. |
| `ragapp/llm/tool_calling/azure_foundry.py` | Shared Azure Foundry endpoint and credential handling. |

**Interaction:** `loop.py` calls `ragapp.llm.provider`; the provider resolves settings from `ragapp/config.py`, selects the adapter named in `registry.py`, and sends normalized messages/tool declarations. Adapters return a common result consumed by the agent loop.

**Refactor checks:** Add contract tests around common adapter result shape, tool-call arguments, missing credentials, provider selection, and throttling before touching shared wire helpers.

## Module 5: Tool Contract, Registration, and File Operations

**Purpose:** Expose application operations to the model with schemas and route them to narrow service functions.

| File | Responsibility |
| --- | --- |
| `ragapp/tools/__init__.py` | `build_default_tools`: central registry of project, VCS, agent, workflow, compile, cognition, and file/document tools. |
| `ragapp/tools/definitions.py` | Shared `Tool` contract. |
| `ragapp/tools/project_files.py` | Project listing/reading, Workspace file/folder operations, and review-gated Source proposals. The file contains repeated definitions/helpers; inspect which definition is active after Python name rebinding before refactoring. |
| `ragapp/tools/workspace_files.py` | Workspace listing and byte-preserving copy operations. |
| `ragapp/tools/regular_files.py` | Text/code/config file read, write, edit, and delete operations. |
| `ragapp/tools/binary_files.py` | Bounded base64 operations and byte-range edits for unsupported binary formats. |
| `ragapp/tools/extractors.py` | Shared format detection and text extraction helpers used by file/cognition flows. |
| `ragapp/tools/docx_files.py` | DOCX read/create/edit/delete tools. |
| `ragapp/tools/pdf_files.py` | PDF read/create/edit/page-copy operations. |
| `ragapp/tools/pptx_files.py` | PPTX read/create/edit/delete operations. |
| `ragapp/tools/xlsx_files.py` | Workbook read/create/cell-edit/delete operations. |
| `ragapp/tools/xml_files.py` | XML read/write/XPath edit/delete operations. |
| `ragapp/tools/document_tools.py` | High-level DOCX-to-artifact transformation tool with preservation validation. |
| `ragapp/tools/vcs_tools.py` | Exposes history, revision, diff, restore, and checkpoint operations to the agent. |
| `ragapp/tools/agent_tools.py` | Agent-definition CRUD tools. |
| `ragapp/tools/workflow_tools.py` | Describes stored workflow steps and their inference/deterministic boundaries. |
| `ragapp/tools/compile_tools.py` | Explicit `recompile_source` tool; selects source files and invokes the cognition compiler. |
| `ragapp/tools/cognition_tools.py` | Active cognition retrieval, knowledge lifecycle, validation, impact, and session tools. |
| `ragapp/tools/cognition_tools.py` (same file) | Note: unlike `ragapp/cognition/cognition_tools.py`, this is the builder imported by the default tool registry. |

**Interaction:** `build_default_tools()` gathers builders; `AgentRunContext` optionally filters tools; `ToolRegistry` validates/dispatches calls. Most file tools use Workspace for writes. Project-file tools provide the special proposal route for agent-generated Source changes.

## Module 6: Cognition Store, Compilation, and Retrieval

**Purpose:** Turn source material and in-session learning into canonical project knowledge, then retrieve bounded relevant portions for later work.

| File | Responsibility |
| --- | --- |
| `ragapp/cognition/__init__.py` | Exports `CognitionStore`. |
| `ragapp/cognition/store.py` | Canonical cognition persistence, source provenance, temporal/entity state, retrieval hydration, and document location access. |
| `ragapp/cognition/compiler.py` | Active source-to-cognition orchestration, file selection/chunking, code/document compiler dispatch, synchronization, and compilation history. |
| `ragapp/cognition/code_parser.py` | Tree-sitter language detection and source symbol/reference parsing. |
| `ragapp/cognition/code_compiler.py` | Compiles deterministic code structure into cognition. |
| `ragapp/cognition/document_compiler.py` | Uses the configured LLM to interpret document segments and persist structured knowledge/provenance. |
| `ragapp/cognition/document_index.py` | Stores document structure and physical locators; intended to avoid persisting full source bodies in the index. |
| `ragapp/cognition/merge.py` | Reconciles/adds durable cognition and handles semantic mutations. |
| `ragapp/cognition/session_memory.py` | Persists/retrieves session-level memory episodes. |
| `ragapp/cognition/classifier.py` | Separate older compilation implementation; no import of this module was found in the active in-repo compile paths. Investigate migration history and tests before removal. |
| `ragapp/cognition/cognition_tools.py` | Alternate cognition tool/helper implementation; current default registry imports `ragapp/tools/cognition_tools.py` instead. Check for external/public imports before consolidating. |
| `ragapp/document_parser.py` | Parses source artifacts into structured nodes, segments, and physical page/section locators for cognition ingestion. This is distinct from `ragapp/documents/parser.py`. |

**Compilation flow:** `compile_project` selects source/workspace files; code files use Tree-sitter parsing and deterministic compilation; supported documents use `document_parser.py` plus model-assisted `DocumentCognitionCompiler`; `CognitionStore` persists canonical records and locations; `MetadataSync` updates searchable projections; VCS records the resulting state.

**Retrieval flow:** The agent chooses cognition tools/context; retrieval searches semantic and metadata projections, expands connected cognition where appropriate, then hydrates a bounded selection from the canonical store. The indexes are derived/search structures; the canonical cognition store remains the source of truth.

## Module 7: Core Project Services and Persistence

**Purpose:** Own reusable project operations beneath the UI/API/tools, keeping workflows consistent where they share these services.

| File | Responsibility |
| --- | --- |
| `ragapp/core/__init__.py` | Package marker. |
| `ragapp/core/metadata.py` | SQLite metadata/search projection operations. |
| `ragapp/core/metadata_sync.py` | Synchronizes/rebuilds metadata projection from canonical cognition. |
| `ragapp/core/semantic_index.py` | Local embedding/vector index operations. |
| `ragapp/core/retrieval.py` | Hybrid retrieval, structural expansion, bounded context hydration, and continuation support. |
| `ragapp/core/instructions.py` | Persistent scoped instructions and applicability selection. |
| `ragapp/core/agents.py` | Persistent project agent definitions. |
| `ragapp/core/workflows.py` | Deterministic workflow prefix executor; no caller found in repository search. |
| `ragapp/core/drafts.py` | Draft persistence and listing/editing state. |
| `ragapp/core/approval.py` | Approves/rejects drafts, validates/promotes changes, records history, and may trigger selective recompile. |
| `ragapp/core/review.py` | Thin review wrappers; no in-repository caller found. |
| `ragapp/core/project_files.py` | Shared Source/Workspace file mutation service with path validation and Git checkpoints. |
| `ragapp/core/vcs.py` | Git-backed project history, checkpoints, diffs, revisions, restore, and forward materialization. |
| `ragapp/core/reingest.py` | Selects affected source files and coordinates recompilation after approval. Some convenience methods (`full`, `affected`) had no direct caller found; `after_approval` is used. |
| `ragapp/chat_sessions.py` | Persists session turns, active branch lineage, titles, and branch operations. |

**Key ownership boundary:** `ProjectFileService` owns direct Source/Workspace file mutation and Git recording for its callers. Agent proposals additionally use `DraftManager` and `ApprovalEngine`. Check whether direct UI/API operations and tool operations all use the same service before modifying file-history behavior.

## Module 8: Structured Document Rendering

**Purpose:** Transform a parsed DOCX into a semantic document model and render the model into a chosen output format without conflating restyling with summarization.

| File | Responsibility |
| --- | --- |
| `ragapp/documents/__init__.py` | Public exports for the Document IR, parser, renderers, and validation. |
| `ragapp/documents/model.py` | Document and block intermediate representation. |
| `ragapp/documents/parser.py` | Parses DOCX content into the shared IR. |
| `ragapp/documents/themes.py` | Theme definitions and lookup. |
| `ragapp/documents/pdf_renderer.py` | Paginated PDF layout. |
| `ragapp/documents/docx_renderer.py` | Structured Word rendering. |
| `ragapp/documents/pptx_renderer.py` | Slide-aware PowerPoint rendering. |
| `ragapp/documents/html_renderer.py` | Semantic HTML rendering. |
| `ragapp/documents/markdown_renderer.py` | Markdown rendering. |
| `ragapp/documents/validator.py` | Output/source coverage and renderer validation. |

**Flow:** `transform_document` resolves a DOCX input, calls `parse_docx`, selects a renderer by destination suffix, applies a theme, validates preservation, and writes the result to Workspace. The high-level transform is currently DOCX-source-only even though dedicated tools separately operate on PDF/PPTX/XLSX files.

## Module 9: Other Extractors and Visualization Helpers

**Purpose:** Provide alternate extraction abstractions and visualization helpers. Their relationship to the active compilation and UI paths needs explicit verification.

| File | Responsibility |
| --- | --- |
| `ragapp/extractors/base.py` | Extractor protocol/base abstraction. |
| `ragapp/extractors/code_extractor.py` | Python AST-oriented extraction. |
| `ragapp/extractors/general_extractor.py` | General structured document extraction. |
| `ragapp/extractors/narrative_extractor.py` | Narrative-aware chunking/extraction. |
| `ragapp/visualization/__init__.py` | Package marker. |
| `ragapp/visualization/mermaid.py` | Mermaid fence/source normalization helper. |
| `ragapp/interfaces/streamlit_app/mermaid.py` | Streamlit Mermaid processing/render support. |

Static in-repository search found no runtime imports for the `ragapp/extractors/*` family or the visualization Mermaid helper. The Streamlit main module has its own Mermaid preview logic. These are review candidates, not confirmed dead code; verify package consumers and intended extension points.

## Module 10: User Interfaces

**Purpose:** Expose the common project runtime through a browser UI, a terminal chat interface, and an HTTP API.

| File | Responsibility |
| --- | --- |
| `ragapp/interfaces/__init__.py` | Package marker. |
| `ragapp/interfaces/launcher.py` | Interactive choice between Streamlit and terminal chat. |
| `ragapp/interfaces/cli/__init__.py` | Package marker. |
| `ragapp/interfaces/cli/terminal.py` | Terminal chat interaction and runtime invocation. |
| `ragapp/interfaces/streamlit_app/__init__.py` | Package marker. |
| `ragapp/interfaces/streamlit_app/main.py` | Main browser UI composition: Chat, Files, Cognition, Review, Instructions, History, Settings, projects, and sessions. |
| `ragapp/interfaces/streamlit_app/auth_ui.py` | Browser sign-in/sign-up flow. |
| `ragapp/interfaces/streamlit_app/file_manager.py` | File browser, operations, and file preview selection. |
| `ragapp/interfaces/streamlit_app/previews.py` | Document preview and reading/original-layout rendering. |
| `ragapp/interfaces/streamlit_app/mermaid.py` | Mermaid diagram handling used by the UI. |
| `api.py` | FastAPI surface; separate root module, not nested under `ragapp/interfaces`. |

**Entrypoint check to perform:** `pyproject.toml` declares `cognitive-streamlit = ragapp.interfaces.streamlit_app.main:main`, while the Streamlit file appears to execute at module import and may not define `main()`. Verify the installed console script actually works; the documented direct `streamlit run ...` command and `run_streamlit.bat` are separate paths.

## Module 11: Build, Tests, and Local Runtime Artifacts

**Purpose:** Define the environment and provide evidence for current behavior.

| File or path | Responsibility |
| --- | --- |
| `pyproject.toml` | Package metadata, Python constraint, dependencies, package discovery, and console scripts. |
| `uv.lock` | Resolved dependency versions. |
| `python-version` | Python version pin. |
| `.env.example` | Template for environment configuration; do not put real credentials here. |
| `.gitignore` | Excludes generated/local state such as environments and project data. |
| `run.bat` | Windows package/launcher helper. |
| `run_api.bat` | Windows API/Uvicorn helper. |
| `run_streamlit.bat` | Windows Streamlit helper. |
| `README.md` | Existing setup and product documentation; contains stale version/provider statements that need reconciliation. |
| `PROJECT DETAILS.md` | Product capabilities, scenarios, caveats, and constraints. |
| `tests/test_chat_branching.py` | Tests branch preservation and VCS forward materialization behavior. |
| `tests/test_document_cognition.py` | Tests PDF source locators, source-text exclusion from index, and metadata-first retrieval. |
| `cognitive_persistence_agent.egg-info/` | Generated installed-package metadata; normally not hand-maintained source. |
| `projects/` | Local runtime project data, not application source. Inspect only as needed and avoid treating user data as disposable test fixtures. |
| Root `*.zip`, `.venv/`, `.env`, `auth.db` | Local archives, environment, secret-bearing configuration, and local database; not code modules. Do not expose or rewrite these while doing a source refactor. |

## How The Main Modules Work Together

### A. Startup and project selection

1. `main.py` or `ragapp/__main__.py` calls the launcher.
2. The launcher starts terminal chat or Streamlit. FastAPI is started separately via Uvicorn and `api.py`.
3. The UI/API resolves a user/project with `execution/project.py`; project paths and current-project context are managed by `workspace/manager.py`.
4. Project-specific configuration and data are loaded from the project's runtime directory.

### B. Chat turn

1. Streamlit, terminal chat, or API builds a transcript and calls the shared agent runtime.
2. `tools.build_default_tools()` collects tool builders. Cognition tools are conditional on an initialized cognition store; file/document tools are generally available independently.
3. `AgentRunContext` loads an optional enabled agent and filters the tool set.
4. `ContextSelector` resolves turn intent and bounded relevant context. Instructions/retrieval services contribute only applicable material.
5. `agent.loop` calls `llm.provider`; the provider picks a configured adapter from the provider registry.
6. The model may return text or tool calls. `ToolRegistry` dispatches calls to tool functions and returns results to the loop.
7. The loop records tool traces, handles durable cognition state updates and review drafts, and returns the final answer.
8. The caller appends session data. UI/API chat paths also capture before/after VCS checkpoints and turn effects.

### C. Agent-assisted project change

1. The agent lists/reads Source or Workspace files through project tools.
2. Workspace tools make working edits; direct tool writes to Source are restricted.
3. A proposal tool creates a draft for a Source edit/new file.
4. The Review UI/API lets a human inspect, edit, approve, or reject that draft.
5. `ApprovalEngine` promotes an approved change, records project history, and can recompile affected source into cognition.
6. `VCSManager` makes prior and resulting states inspectable/recoverable.

The API also exposes direct project-file operations that can target Source. This is outside the ordinary agent proposal flow and must be accounted for in authorization and deployment design.

### D. Cognition compilation and query

1. The UI or `recompile_source` tool selects files.
2. `cognition.compiler` routes code through `code_parser`/`CodeCognitionCompiler` and documents through `document_parser`/`DocumentCognitionCompiler`.
3. `CognitionStore` persists canonical entities, events, relations, temporal attributes, and provenance.
4. Metadata and vector services maintain searchable projections.
5. Later, retrieval selects candidates from projections, expands structural links, and hydrates selected canonical records under context limits.

### E. Artifact transformation

1. The model invokes the high-level `transform_document` tool for a DOCX source.
2. The document parser builds the shared IR.
3. A theme and destination format select the renderer.
4. The validator checks preservation; failures should not be treated as delivered output.
5. Successful artifacts are written into Workspace for preview/review/use.

## Initial Static Review Findings

Re-reviewed against the current source on 2026-10-01. The worktree had no modified tracked Python files at review time, so there were no pending source diffs to compare. A syntax compilation check passed for the flagged Python modules. The findings below distinguish concrete defects from incomplete features and unverified compatibility surfaces; none is automatic authorization to delete code.

| Candidate | Current issue/status | Recommended next action |
| --- | --- | --- |
| `ragapp/agent/router.py` | **Unwired alternate router.** It contains request classification and tool allowlisting, but no in-repository caller was found. `agent/loop.py` uses `ContextSelector` instead. The duplicated routing policies can drift, but this is not evidence of a current runtime failure because this module is not on the active path. | Decide whether to merge its policy into `ContextSelector` or retain it as an external/experimental API. Add routing tests before integration and check downstream imports before removal. |
| `ragapp/core/workflows.py` | **Incomplete advertised capability.** `run_deterministic_prefix` executes `set` and tool steps and stops at an inference step, but has no caller. Agent specs store steps and a tool describes them; selecting an agent does not execute them. Trigger metadata is not shown driving a scheduler/event runner. | Wire execution into the run contract with approval/tool-policy tests, or describe workflows as stored/descriptive only and defer claims of automatic execution. |
| `ragapp/cognition/classifier.py` | **Legacy duplicate compiler candidate.** It contains a separate older compilation prompt/pipeline. Current UI, API, and `recompile_source` paths use `ragapp/cognition/compiler.py`; no import of this module was found. The risk is schema/behavior drift between implementations, not a confirmed failure in the active compiler. | Compare output schemas and on-disk compatibility, check external imports, then migrate callers or retire it with a compatibility note. |
| `ragapp/cognition/cognition_tools.py` | **Parallel implementation candidate.** The default registry imports `ragapp/tools/cognition_tools.py`, while this module defines another cognition tool builder and helpers. The duplicate surfaces can diverge; no active in-repository caller was found for this copy. | Compare mutation, retrieval, validation, impact, and session-memory behavior; consolidate only after checking external imports. |
| `ragapp/core/review.py` | **Redundant facade, not a demonstrated bug.** It is a thin wrapper around `DraftManager` and `ApprovalEngine`; UI/API instantiate those services directly, and no in-repository use of the wrapper was found. It is harmless if intentionally public but creates a second apparent entry point. | Decide whether it is a supported service facade. If internal-only and unused, remove in a separately tested cleanup; otherwise route callers through it and test parity. |
| `ragapp/core/reingest.py` | **Mixed status.** `after_approval()` is used; `full()` and `affected()` have no callers found. `affected()` returns `status: scheduled` and entity IDs but does not schedule or execute work, which may mislead callers. `tools/compile_tools.py` also calls the private `_source_files()` helper. | Clarify whether `affected()` plans, queues, or completes work and make its status explicit. Consider exposing a public source-list method before changing the private-helper dependency. |
| `ragapp/agent/cognitive_cycle.py` | **Partially exercised lifecycle tracker.** The loop constructs it and advances it at persistence; `briefing()` has no call site. Its declared full lifecycle is not visibly driven through the full turn. | Decide whether stages are diagnostic or operational. Wire and test transitions, or narrow/remove the unused briefing and full-cycle claims. |
| `ragapp/clients.py` | **Likely intentional compatibility shim.** No internal caller was found, but the module labels itself backward-compatible, keeps the old module-level `client` as `None`, and directs new code to the provider service. Lack of internal use alone does not make it stale. | Keep unless a breaking-release decision and external import check support removal. Add a deprecation path if needed. |
| `ragapp/extractors/base.py`, `code_extractor.py`, `general_extractor.py`, `narrative_extractor.py` | **Unwired alternate extraction family.** No imports/call sites were found. Active cognition compilation uses `cognition/compiler.py`, `code_parser.py`, `code_compiler.py`, `document_parser.py`, `document_compiler.py`, and `tools/extractors.py`. This is not evidence of an active-path failure. | Map output contracts against active parsers and check external consumers. Choose a single extractor ownership model before extending either family. |
| `ragapp/visualization/mermaid.py` and `ragapp/interfaces/streamlit_app/mermaid.py` | **Two unreferenced Mermaid helpers; separate active preview exists.** No imports of either helper were found. `streamlit_app/main.py` contains/calls its own `_render_mermaid_preview`, and `ContextSelector` provides Mermaid prompt guidance. Mermaid preview is therefore present; helper validation/normalization is the unwired part. | Decide whether to integrate the validation helper with the active preview. Consolidate around one tested implementation or remove only after checking external imports. |
| `ragapp/tools/project_files.py` | **Concrete maintainability defect.** Repeated implementation blocks and two `build_project_file_tools()` definitions mean Python retains the later binding and shadows earlier definitions. A copied block after an unconditional `return` inside `_workspace_edit` is unreachable. This invites edits to inactive code and behavioral drift. Syntax compilation passes, so this is not a syntax error. | Add focused tests for tool registration, path containment, Source/Workspace write boundaries, proposals, and copy/move. Then consolidate while preserving the active implementation's behavior. |
| Streamlit console-script target in `pyproject.toml` | **Likely broken installed entry point.** `cognitive-streamlit` targets `ragapp.interfaces.streamlit_app.main:main`, but that module has no `def main(...)`. Direct `streamlit run ragapp/interfaces/streamlit_app/main.py` is a separate path and may work. | Test `uv run cognitive-streamlit`. If it fails, point the script at a real wrapper or remove it and document the supported direct command. |
| `README.md` provider/version statements | **Documentation drift.** Package metadata says 9.5.5 while README retains 9.5.3 references. README says Anthropic is disabled, but registry/adapter files include Anthropic and Azure Anthropic. Code presence does not prove credentials or deployments work. | Reconcile versions and distinguish implemented adapters from providers verified against configured credentials. |

Repository searches only establish that no static in-repository caller was found. They do not rule out reflection, plugin loading, scripts outside this repository, or external package consumers. Treat each unwired item as an investigation result, not proof of dead code.

## Recommended Pre-Refactor Analysis Sequence

### Phase 1: Establish the baseline

1. Record the current working tree and distinguish pre-existing user changes from analysis edits.
2. Confirm Python version and install state with the documented `uv` environment.
3. Run syntax compilation and the existing test suite; save exact commands/results.
4. Smoke-test the launcher, Streamlit direct command, console script, and API separately. Do not expose the API publicly during this check.
5. Capture representative behavior for one chat query, one workspace edit/proposal/review, one cognition compile/retrieval query, and one document transformation.

### Phase 2: Build ownership and dependency evidence

1. For each module above, record public symbols, inbound imports/calls, outbound dependencies, filesystem writes, provider calls, and user-visible entry points.
2. Trace duplicate implementations and shadowed definitions with symbol-level references rather than file names alone.
3. Mark each capability as active, optional, legacy/compatibility, incomplete, or unresolved, and cite the evidence/test for that classification.
4. Check packaging entrypoints and public imports as well as normal source call sites.

### Phase 3: Agree intended behavior before cleanup

1. Use [PROJECT DETAILS.md](PROJECT%20DETAILS.md) to confirm which user-visible scenarios are in scope.
2. Convert required behaviors into focused tests, especially review boundaries, session branches, retrieval provenance, format fidelity, and API authorization.
3. Decide whether incomplete workflow features should be completed, explicitly deferred, or removed from user-facing definitions.
4. Decide the intended contract for legacy/duplicate extraction and cognition tools.
5. Keep security remediation (notably API authentication/authorization) distinct from cosmetic/module cleanup, but address it before any untrusted deployment.

### Phase 4: Refactor one ownership boundary at a time

1. Start with narrow, well-covered leaf modules, not the agent loop or canonical store.
2. Change one module boundary, then run its focused tests and the existing suite before continuing.
3. Preserve on-disk project formats and migration compatibility unless a separately tested migration is approved.
4. Use import/reference checks plus behavior tests before removing a candidate.
5. Update `README.md`, `PROJECT DETAILS.md`, and this map only after code behavior has been verified.

### Phase 5: Validate the resulting product

- Run unit tests, syntax compilation, and API/UI startup checks.
- Verify supported provider adapters with mocked contract tests; live tests require explicit credentials and should not leak secrets.
- Exercise one end-to-end flow per major user scenario.
- Recheck Source/Workspace authorization boundaries and direct API routes.
- Search again for stale imports, shadowed definitions, dead compatibility code, and inaccurate documentation.
- Report what was changed, what remains incomplete, and which test/smoke checks were actually executed.

## Scope Notes for the Future Refactor

- This file is an orientation artifact, not a promise that every listed feature is production-ready.
- Do not use local `projects/` contents, `.env`, `auth.db`, archives, or generated package metadata as disposable refactor targets.
- Avoid broad renames until entrypoint/import contracts are known.
- A file with no local caller may still be an external integration surface; search package metadata, docs, scripts, and downstream usage before removal.
- Preserve user changes in the worktree. Refactor commits and destructive cleanup are not part of this pre-analysis phase.