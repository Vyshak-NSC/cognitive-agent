# Cognitive Persistence Agent: Project Details

## 1. Purpose

Cognitive Persistence Agent (CPA) is a local, project-scoped AI workspace. It combines chat with persistent project knowledge, file and document tools, and project history. Its central purpose is to let an AI assistant work across multiple sessions without treating each conversation as the only source of truth.

The application can ingest project material into structured cognition, retrieve relevant knowledge during later conversations, perform tool-assisted work, and keep proposed source edits reviewable. Project data is stored separately from the application's Python code, under the configured projects root (by default, `./projects`).

CPA is useful when a person or team repeatedly asks questions about the same body of material, needs the assistant to remember domain facts and decisions, or wants to draft changes and inspect them before making them authoritative.

## 2. What Runs

The repository has three user-facing entry paths:

| Entry path | What it provides | How it is started |
| --- | --- | --- |
| Streamlit application | Main interactive UI: project and chat navigation, files, cognition, review, instructions, history, and settings | `uv run cognitive-streamlit` or `uv run streamlit run ragapp/interfaces/streamlit_app/main.py` |
| Terminal chat | A terminal-oriented chat interface, launched from the launcher | `uv run cognitive-agent`, then select terminal chat |
| FastAPI application | HTTP API for chat and project operations | `uv run uvicorn api:app --reload` |

The launcher in `main.py` offers Streamlit and terminal chat. The Streamlit UI includes account login/registration and project management. A command-line helper exists for creating accounts (`python -m ragapp.auth.cli`).

The current package metadata declares version **9.5.5**, Python **3.12.x**, and the package name `cognitive-persistence-agent`. The README still contains several 9.5.3 references, so those version-specific statements should be treated as historical documentation until reconciled.

## 3. Project Data and Boundaries

Each project is an isolated working context. Its data includes some or all of the following:

- **Source**: authoritative project inputs and source files.
- **Workspace**: editable working files, generated artifacts, and AI-produced drafts.
- **Cognition**: structured, persistent knowledge compiled from inputs or recorded during work.
- **Sessions**: conversations, branches, tool traces, and session-specific memory.
- **System metadata**: project configuration, agent definitions, indexes, and related runtime data.
- **Git history**: checkpoints and revisions for project state and files.

The intended agent-editing flow is:

```text
read Source -> create or edit in Workspace -> propose a Source change -> review -> approve or reject
```

An agent's ordinary project-file write tools are workspace-scoped. Agent-generated source edits are staged as review drafts; approval promotes them to Source. This boundary is not a universal restriction on every interface: the API's project-file service accepts both `source` and `workspace` areas for direct operations. Deployments should protect direct API access accordingly.

## 4. Persistent Cognition

### 4.1 What cognition contains

The cognition store is a structured model of a project, rather than a saved transcript. Depending on what is ingested or recorded, it can contain:

- Entities such as people, concepts, files, modules, classes, functions, locations, rules, and artifacts.
- Relationships and dependencies between entities.
- Events and changes, including temporal ordering when the source supports it.
- Concepts, definitions, facts, evidence, hypotheses, decisions, and unresolved questions.
- State, timelines, source provenance, and references back to input artifacts.

This is useful for questions like “What decision did we make about the import pipeline, and why?” or “What could be affected if this module changes?” The answer can be assembled from durable project records rather than relying only on the currently visible chat transcript.

### 4.2 Compiling source material

The cognition compiler extracts text from supported project artifacts, chunks large content to avoid silently truncating it, and asks the configured language model to produce structured knowledge. Compiled records retain provenance to the file and, where available, section or page location. Recompilation can target selected files or all source material, merges into existing cognition, and is designed to be additive rather than erasing prior knowledge.

Compilation can take time and consume provider tokens. It is available from the Cognition screen and through the `recompile_source` agent tool. Normal chat and file operations can still be used before cognition has been compiled.

### 4.3 Retrieval and inspection

The project includes local semantic indexing (FastEmbed and SQLite vector support), metadata-oriented search, structural expansion through connected cognition, and bounded context loading for agent turns. The intended retrieval flow is:

```text
question -> select relevant capabilities and cognition -> expand related records -> load bounded context -> answer or use tools
```

The system also provides cognition inspection and maintenance operations, including world-model snapshots, state maps, ledgers, validation, contradiction candidates, and impact analysis. Some of these are available through the UI, API, or agent tools rather than all appearing in one screen.

### 4.4 Knowledge lifecycle

The cognition tools can preserve investigation and change history as durable records:

```text
observation/evidence -> hypothesis -> confirming or disconfirming evidence
                    -> fact or open question -> decision -> implementation/change
```

For example, during a debugging investigation the agent can record that a failure occurs only with empty CSV headers as evidence, record a hypothesis that header normalization is responsible, then update that hypothesis after testing. A resolved conclusion can be stored as a fact, and the selected fix as a decision/change. This makes the reasoning discoverable in a later session.

## 5. Chat, Sessions, and Agent Runtime

### 5.1 Tool-using chat

Chat is backed by an agent loop that can retrieve project context, follow persistent instructions, call registered tools, and return the tool calls and results as a trace. Project mutation intent can route ordinary chat into the file inspection and review workflow; users do not need to create a custom agent just to ask for a project edit.

Example request:

> “Read the CSV parser, add a check for an empty header row, and stage the change for review.”

The agent can inspect Source, copy/edit the relevant file in Workspace, and submit a review proposal. It should not report a change as complete merely because it described one; the runtime tracks successful tool calls and generated proposals.

### 5.2 Persistent sessions and branching

Chat sessions are stored per project. The UI supports creating, renaming, selecting, and deleting sessions. Conversation turns carry identifiers and project-state checkpoints. Editing an earlier turn can create a sibling branch while preserving the original branch; activating a branch can materialize its corresponding project state as a new Git transition instead of rewriting prior history.

This is useful for comparing alternate approaches: preserve the original conversation and project state, edit an earlier request, and continue along a separate branch.

### 5.3 Persistent instructions

Project instructions persist independently of chat sessions. The UI supports adding, editing, and deactivating instructions. Instruction scopes include system-wide project guidance, situational guidance, and guidance associated with a tagged entity. Applicable instructions are loaded into the runtime context for agent work.

Example instruction: “For database changes, include a reversible migration and a test for the previous schema.” That directive can guide later sessions without being repeated.

### 5.4 Custom agents

Project-scoped agent definitions can store a name, objective, instructions, data-source/output-target guidance, allowed and denied tools, workflow-step metadata, trigger metadata, enabled status, and whether mutations require approval. The Streamlit sidebar can select an enabled agent for chat. The runtime applies allowed/denied tool filters before passing tool declarations to the model, and carries the selected agent's guidance into the run.

Agents are useful for consistent roles such as “documentation reviewer” with a restricted tool set or “release assistant” with explicit project instructions. See Section 11 for workflow execution limitations.

## 6. Files and Documents

### 6.1 General project files

The file tools and Files screen support listing, reading, creating, editing, copying, moving, renaming, and deleting project files, with separate Source and Workspace areas. Text/code/config files have regular-file operations. Unsupported binary files have base64 read/write and byte-range edit operations; format-aware tools should be preferred when available.

`read_project_text` can extract readable text from several office/document formats instead of treating them as plain text bytes. The Streamlit file browser also provides previews for common document types and HTML/Markdown content.

### 6.2 Format-specific operations

Format-aware capabilities are registered in the default agent toolset:

| Format | Examples of available operations |
| --- | --- |
| DOCX | Read, create, edit structured content, and delete workspace documents |
| PDF | Read whole files or page ranges, create/render PDFs, edit pages or overlays, and copy page ranges |
| PPTX | Read slides, create/edit presentations, and delete workspace presentations |
| XLSX | Read workbook cells, create/edit cells, and delete workbooks |
| XML | Read, write, and make XPath-based element, attribute, or text edits |
| Other binary formats | Base64 read/write, byte-range edit, and delete where supported |
| Plain text/code/config | Read, create, precise edit operations, and delete in the workspace |

Exact editing behavior is format-dependent. For example, the spreadsheet editor changes individual cells while retaining workbook formatting, and XML editing uses XPath-oriented operations to preserve document structure.

### 6.3 Structured artifact transformation

The `transform_document` tool parses a **DOCX source** into a shared Document IR and can render the result into PDF, DOCX, PPTX, HTML, or Markdown. It supports the `professional`, `minimal`, `academic`, `report`, and `fantasy_codex` themes. For transformations, content preservation is enabled by default and the result is checked for source coverage; an explicit condensation request is needed to allow content reduction.

Example:

> “Convert `Source/OperationsGuide.docx` into a professional PDF in the workspace, preserving all sections.”

The tool validates the rendered output and removes a failed output instead of returning it as a successful transformation. The structured transformation entry point currently accepts DOCX sources; it does not claim arbitrary PDF/PPTX/XLSX-to-anything conversion. Separate format-specific tools provide operations on those other formats.

## 7. History, Review, and Change Recovery

The project VCS layer records file and project-state history. Available operations include logs, file history, revision content, diffs, restoring a prior file as a new revision, and explicit checkpoints. Chat turns can also record before/after state checkpoints and effects such as tool counts and draft IDs.

Review drafts can be inspected, edited, approved, or rejected. An approval promotes the reviewed change into Source; rejecting a proposal can preserve a reason. This is suited to workflows where AI-generated changes need human sign-off before becoming authoritative.

Example: ask for a rewrite of `source/README.md`, inspect the proposed content in Review, edit its wording, then approve it. The approved content is committed through the approval layer, with project history available for later inspection.

## 8. LLM Providers and Configuration

Provider selection and model configuration are project-scoped, and provider calls are throttled using project/provider limits. The current provider registry and adapter files include:

- Google Gemini.
- OpenAI.
- OpenRouter.
- Anthropic.
- Azure OpenAI through Azure Foundry.
- Azure Anthropic through Azure Foundry.

An API key for a configured provider is required for model-backed chat, cognition compilation, and other operations that invoke an LLM. Configuration can be supplied through project settings or environment variables. Never commit credentials in `.env` or project configuration.

The adapter inventory in the current code contradicts the README note that Anthropic is disabled. Actual connectivity still depends on valid provider credentials, endpoint/deployment settings where applicable, and a model name supported by that provider. Provider/model defaults in code are configuration defaults, not a guarantee that every account can access those models.

## 9. HTTP API and Authentication

`api.py` exposes endpoints for health checks, projects, agent queries, draft review, instructions, project history and file operations, source ingestion, cognition snapshots/validation/impact, sessions and session memory, checkpoints, and agent definition CRUD. The API chat route uses the same agent loop and default tool builder as the UI and stores session turns with state checkpoints.

The Streamlit application has login/registration support, and user passwords are stored as hashes with salts. However, the routes in the current `api.py` do not show authentication dependencies or token checks. Do not expose this API to an untrusted network without adding and validating an authentication/authorization layer (or placing it behind a correctly configured trusted gateway). Also note that API project file writes support a selected `source` area as well as `workspace`, so the agent review boundary alone does not protect direct API callers.

## 10. Example Scenarios

### Project knowledge assistant

1. Create a project and add policies, notes, source documents, or code under Source.
2. Compile selected files in Cognition.
3. Ask questions such as “What does the deployment guide say about rollback?”
4. Ask for supporting context or related concepts; cognition records carry provenance back to source files and locations when available.

### Long-running software maintenance

1. Add a repository snapshot to the project.
2. Compile source files so modules, symbols, and relationships can be retrieved.
3. Ask the agent to inspect a component and propose a focused change.
4. Review the draft, approve it, and use Git-backed history to inspect or restore earlier content.
5. Preserve decisions, known facts, and open questions in cognition for future maintenance sessions.

### Document production

1. Place a DOCX guide in Source.
2. Ask for a themed PDF, HTML, or slide-deck transformation.
3. Let the structured renderer preserve headings, paragraphs, lists, tables, and sections.
4. Inspect the generated workspace artifact and its preservation validation before sharing it.

### Research or requirements investigation

1. Add reports, specifications, or notes to a project.
2. Compile or retrieve targeted sections.
3. Record evidence, hypotheses, decisions, and open questions while investigating.
4. Return in a later session and continue from durable context instead of reconstructing the investigation from chat history.

### Spreadsheet or structured-data maintenance

1. Add an XLSX or XML artifact.
2. Read specific sheets/cells or inspect structured XML.
3. Make targeted changes with the appropriate format-specific tool.
4. Keep the resulting file in Workspace or submit an agent-created source proposal for review.

## 11. Implemented but Not Fully Wired, and Important Limits

These distinctions matter when describing what the application can do today:

- **Workflow execution is incomplete.** Agent records store `workflow_steps` and trigger metadata, and a deterministic prefix runner exists in `ragapp/core/workflows.py`. In this repository, that runner has no call site. The runtime can describe workflow-step boundaries, but the stored steps are not automatically executed as a complete agent workflow. Do not rely on schedule/event triggers.
- **Some cognition tools are explicitly legacy compatibility tools.** `get_state_map` and `get_ledger` are still registered, but their descriptions direct the model to use metadata search and targeted context requests instead. Their presence is for compatibility, not the preferred retrieval workflow.
- **Cognition is optional and not pre-populated.** A new project can chat and manage files without compiled cognition, but knowledge-specific retrieval is limited until source files are compiled or facts are recorded.
- **Transformation is not universal conversion.** The high-level structured transform accepts DOCX input and renders PDF, DOCX, PPTX, HTML, or Markdown. Other formats have their own readers/editors, but this does not imply every source/target conversion pair is supported.
- **The standalone terminal UI is a launcher option, not the same screen set as Streamlit.** The full project-management navigation is in the Streamlit interface.
- **The repository test suite is small relative to the feature surface.** The checked-in tests cover chat branching/state restoration and document cognition/retrieval behaviors, but do not constitute complete end-to-end coverage of every provider, file tool, API route, renderer, or workflow scenario.
- **No automatic production deployment is provided by the described entry points.** The repository includes local application and API launch paths; deployment, network hardening, operational monitoring, and API access policy must be supplied by the operator.

“Not fully wired” above is based on the checked-in call paths, not a claim that every unmentioned module is dead code. A repository search cannot prove that optional helpers are never used dynamically or by external consumers.

## 12. Requirements and Operational Notes

- Python 3.12.x is declared as the supported runtime.
- `uv` is the documented environment and dependency manager; `uv sync` installs the locked environment.
- At least one enabled LLM provider credential is needed for LLM-backed tasks.
- Runtime project data defaults to `./projects` and is separate from application source code.
- Avoid placing secrets in source material that will be sent to an external model provider unless that provider and configuration are approved for the data.
- Cognition compilation and chat can send project-derived context to the configured model provider; local embeddings do not mean all model processing is local.

## 13. In One Sentence

CPA is a persistent, project-aware AI workbench for retaining structured knowledge, answering questions over project material, manipulating files and documents, recording decisions and investigations, and staging agent-generated source changes for review, with local project history to make those activities inspectable and recoverable.