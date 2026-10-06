"""Agent loop: scoped cognition context -> tool calls -> persisted state/drafts.
The agent uses normal model turns with scoped cognition retrieval.
Persistent project instructions are loaded locally from InstructionStore and
passed to the model as part of the actual system instruction.
Tool calls are preserved exactly as executed so the UI can display:
    - which tool was called
    - the arguments supplied to it
    - the returned result
Draft content is only created from an actual workspace file when a
DRAFT_METADATA block identifies that workspace file.
Durable cognition changes are applied through STATE_UPDATE blocks.
"""
from __future__ import annotations
import json
import logging
import re
from ragapp.agent.registry import ToolRegistry
from ragapp.llm.tool_calling import (
    to_provider_contents,
    build_function_response_content,
)
from ragapp.llm.prompts import COGNITIVE_AGENT_PROMPT
from ragapp.llm.provider import generate_step as provider_generate_step
from ragapp.settings import MAX_AGENT_STEPS, MAX_CONTEXT_CHARS
from ragapp.core.drafts import DraftManager
from ragapp.core.instructions import InstructionStore
from ragapp.agent.cognitive_cycle import CognitiveCycle
from ragapp.cognition.session_memory import SessionMemory
from ragapp.core.retrieval import RetrievalStore
from ragapp.agent.context_selector import ContextSelector, _looks_like_cognition_mutation, _requests_recompile, _looks_like_document_transform
from ragapp.agent.run_context import AgentRunContext
from ragapp.agent.trace import trace_event, trace_scope
from ragapp.tools.definitions import Tool
from ragapp.logging_config import configure_logging

configure_logging()
LOGGER = logging.getLogger("ragapp.agent.loop")
_PAGED_SOURCE_TOOLS = {"read_source_content", "read_source_pdf_chapter"}
_DIRECT_PDF_PAGE_TOOLS = {"show_source_pdf_pages"}
_MAX_AUTO_SOURCE_CHUNKS = 32


def _emit_source_chunk(on_section, result, chunk_number):
    """Send a source excerpt to a live UI without placing it in model context."""
    if not on_section or not isinstance(result, dict) or not result.get("text"):
        return
    title = result.get("title") or result.get("path") or "Source document"
    try:
        on_section({
            "type": "section",
            "title": f"{title} - part {chunk_number}",
            "content": f"### {title} - part {chunk_number}\n\n{result['text']}",
        })
    except Exception:
        # Rendering must never make a source read fail.
        LOGGER.exception("Could not render source chunk path=%s", result.get("path"))
class EmptyResponseError(Exception):
    pass
def generate_step(*args, **kwargs):
    try:
        return provider_generate_step(*args, **kwargs)
    except Exception as exc:
        if exc.__class__.__name__ == "EmptyResponseError":
            raise EmptyResponseError(str(exc)) from exc
        raise
def _extract_blocks(text, tag):
    """Extract every <tag>...</tag> block.
    Returns:
        (list_of_raw_block_contents, text_with_all_blocks_removed)
    """
    if not text:
        return [], ""
    blocks = [
        m.group(1).strip()
        for m in re.finditer(
            rf"<{tag}>\s*(.*?)\s*</{tag}>",
            text,
            re.S,
        )
    ]
    cleaned = re.sub(
        rf"\s*<{tag}>.*?</{tag}>\s*",
        "",
        text,
        flags=re.S,
    ).strip()
    return blocks, cleaned
def _json_safe(value):
    """Convert arbitrary tool results into JSON-serializable values."""
    if value is Ellipsis:
        return None
    if isinstance(value, dict):
        return {
            str(k): _json_safe(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _json_safe(v)
            for v in value
        ]
    if isinstance(value, set):
        return [
            _json_safe(v)
            for v in value
        ]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
def _read_workspace_snapshot(cognition, target_file):
    """Read a target workspace file safely.
    Returns None if:
      - target_file escapes the workspace
      - target_file does not exist
      - target_file cannot be read
    """
    if cognition is None:
        return None
    try:
        resolved = (
            cognition.workspace / target_file
        ).resolve()
        resolved.relative_to(
            cognition.workspace.resolve()
        )
    except (ValueError, RuntimeError, AttributeError):
        return None
    if not resolved.is_file():
        return None
    try:
        return resolved.read_text(
            encoding="utf-8"
        )
    except UnicodeDecodeError:
        return resolved.read_bytes().decode(
            "utf-8",
            errors="replace",
        )
def _latest_user_query(transcript):
    for message in reversed(transcript or []):
        if str(message.get("role", "")).lower() == "user":
            return str(message.get("content", "") or "").strip()
    return ""


def _asserts_known_entity_state(query, cognition):
    """Recognize a short declarative assertion about an entity already in cognition.

    This is intentionally structural, not semantic: the semantic mutation engine still
    decides what the assertion means and which connected cognition must change.
    """
    q = str(query or "").strip()
    if not q or cognition is None or not cognition.exists():
        return False
    lower = q.lower()
    if "?" in q or re.match(r"^(what|who|why|how|when|where|is|are|does|do|can|could|would|should)\b", lower):
        return False
    # Remove an operational compile clause; the remaining declarative clause may be
    # an authoritative correction (e.g. "Ash is 22. recompile").
    semantic_text = re.sub(_COMPILE_TAIL, "", q).strip(" .;,:")
    if not semantic_text or not re.search(r"(?i)\b(is|are|has|have|became|becomes|was|were)\b", semantic_text):
        return False
    try:
        entity_ids = sorted((cognition.master_metadata().get("entities") or {}).keys())
        for entity_id in entity_ids:
            entity = cognition._read_entity(entity_id) or {}
            names = {str(entity_id), str(entity.get("name") or "")}
            for name in names:
                name = name.strip().lower()
                if name and re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", semantic_text.lower()):
                    return True
    except Exception:
        return False
    return False


_COMPILE_TAIL = r"(?i)[\s,;.]*(?:\b(?:and|then|also|&)\b\s+)?\b(?:re-?compil(?:e|ed|es|ing)|compil(?:e|ed|es|ing)|rebuil[dt]|re-?ingest(?:ed)?)\b.*$"
_EDIT_VERB = re.compile(r"(?i)\b(change|set|update|make|correct|edit|modify|rename|revise|fix|swap|increase|decrease|make)\b")


def _edits_known_entity(query, cognition):
    """Imperative edit that names an entity already in cognition ("change age of Ash to 20").

    Structural only. The semantic mutation engine decides what else must change.
    Code/UI edits ("change the button") are left to the project-mutation path.
    """
    q = str(query or "").strip()
    if not q or "?" in q or cognition is None or not cognition.exists():
        return False
    text = re.sub(_COMPILE_TAIL, "", q).strip(" .;,:")
    if not _EDIT_VERB.search(text):
        return False
    from ragapp.agent.context_selector import _looks_like_project_mutation
    if _looks_like_project_mutation(text):
        return False
    try:
        from ragapp.cognition.merge import _named_entity_seeds
        return bool(_named_entity_seeds(cognition, text))
    except Exception:
        return False


def _semantic_change_text(query):
    """Remove compile-only wording before sending a compound command to mutation planning."""
    text = re.sub(_COMPILE_TAIL, "", str(query or "")).strip(" .;,:")
    return text or str(query or "").strip()



def _requests_chat_cognition(query):
    """Detect an explicit request to learn/compile durable cognition from chat itself."""
    q = str(query or "").strip().lower()
    if not q:
        return False
    learn = re.search(r"\b(generate|compile|create|build|learn|import|distill|extract|save|store)\b", q)
    cognition = re.search(r"\b(cognition|knowledge|memory|canon|canonical|lore|facts?|entities|relationships|events)\b", q)
    chat = re.search(r"\b(this chat|the chat|chat|conversation|conversation history|these messages|our conversation)\b", q)
    source_only = re.search(r"\b(from|using)\s+(the\s+)?(source|documents?|files?)\b", q)
    return bool(learn and cognition and chat and not source_only)


def _load_persistent_instructions(cognition):
    """Load active persistent instructions locally."""
    if cognition is None or not cognition.exists():
        return []
    try:
        instructions = InstructionStore(
            cognition
        ).applicable()
    except Exception:
        return []
    return instructions or []
def _build_system_instruction(active_instructions, modules=None):
    """Build a bounded system instruction from the invariant kernel plus selected context."""
    system_instruction = COGNITIVE_AGENT_PROMPT
    system_instruction += "\n\nNever reproduce internal tool results, retrieval envelopes, JSON context payloads, or controller metadata in the user-facing answer."
    system_instruction += (
        "\n\nWhen the user explicitly approves applying one or more arbitrary changes to canonical cognition "
        "(including deleting an entity/fact/relationship or changes whose consequences may affect connected cognition), "
        "emit exactly one <STATE_UPDATE> JSON block with a semantic_changes array containing the approved changes in plain language. "
        "Do not manually enumerate guessed cascade edits as deltas; the cognition transaction runtime loads connected cognition, "
        "performs semantic cascade planning with the LLM, applies it atomically, cleans references, and commits the new state. "
        "Use ordinary deltas only for simple isolated entity attribute state observations where no semantic cascade is requested."
    )
    module_lines = [str(x).strip() for x in (modules or []) if str(x).strip()]
    if module_lines:
        system_instruction += "\n\nTASK-RELEVANT RUNTIME GUIDANCE:\n" + "\n".join(f"- {x}" for x in module_lines)
    lines = []
    for instruction in active_instructions or []:
        content = instruction.get("content", "") if isinstance(instruction, dict) else str(instruction)
        content = str(content).strip()
        if content:
            lines.append(f"- {content}")
    if lines:
        system_instruction += "\n\nAPPLICABLE PROJECT INSTRUCTIONS:\n" + "\n".join(lines)
    return system_instruction

def _record_tool_call(
    calls,
    name,
    args,
    result,
):
    """Record the actual tool call for the UI/API."""
    safe_args = _json_safe(args)
    safe_result = _json_safe(result)
    calls.append(
        {
            "tool": str(name),
            "args": safe_args,
            "result": safe_result,
        }
    )
    trace_event(
        "tool_finished",
        tool=str(name),
        arguments=safe_args,
        result=safe_result,
    )
def _split_state_deltas(deltas):
    """Split deltas into (plain-language cascading changes, deltas safe to merge directly)."""
    cascade, plain = [], []
    additive = {"create_entity", "create_relationship", "create_event", "create_knowledge"}
    for d in deltas or []:
        if (isinstance(d, dict) and d.get("permanence", "transient") == "permanent"
                and d.get("operation") not in additive and d.get("entity") and d.get("field")):
            reason = str(d.get("reason") or "").strip()
            cascade.append(f"Set {d['field']} of {d['entity']} to {d.get('new')!r}." + (f" {reason}" if reason else ""))
        else:
            plain.append(d)
    return cascade, plain


def _persist_state_updates(
    state_blocks,
    cognition,
    project_id,
    calls,
):
    """Apply STATE_UPDATE blocks to durable cognition.
    Each block is passed to merge_deltas. Entity JSON files are never
    rewritten directly here.
    """
    if not state_blocks:
        return 0
    if cognition is None or not cognition.exists():
        for state_raw in state_blocks:
            _record_tool_call(
                calls,
                "cognition.merge_state_deltas",
                {
                    "error": (
                        "cognition store does not exist"
                    )
                },
                {
                    "error": (
                        "Cannot apply STATE_UPDATE: "
                        "cognition store does not exist."
                    )
                },
            )
        return 0
    from ragapp.cognition.merge import merge_deltas
    applied = 0
    for state_raw in state_blocks:
        try:
            payload = json.loads(state_raw)
            if not isinstance(payload, dict):
                raise ValueError(
                    "STATE_UPDATE must contain a JSON object."
                )
            deltas = payload.get(
                "deltas",
                [],
            )
            events = payload.get(
                "events",
                [],
            )
            semantic_changes = payload.get("semantic_changes")
            if semantic_changes:
                from ragapp.cognition.merge import apply_semantic_mutation
                result = apply_semantic_mutation(
                    cognition,
                    semantic_changes,
                    source_label=f"agent:{project_id}",
                )
            else:
                if not isinstance(deltas, list):
                    raise ValueError(
                        "STATE_UPDATE.deltas must be an array."
                    )
                # A durable attribute change can falsify connected relationships
                # and descriptions, so it goes through the cascading semantic
                # transaction instead of a bare attribute append.
                cascade, plain = _split_state_deltas(deltas)
                result = None
                if plain or events:
                    result = merge_deltas(
                        cognition,
                        plain,
                        source_label=f"agent:{project_id}",
                        events=events,
                    )
                if cascade:
                    from ragapp.cognition.merge import apply_semantic_mutation
                    result = apply_semantic_mutation(
                        cognition, cascade, source_label=f"agent:{project_id}",
                    )
            _record_tool_call(
                calls,
                "cognition.merge_state_deltas",
                payload,
                result,
            )
            applied += 1
        except TypeError as exc:
            # Compatibility fallback applies only to legacy ordinary deltas.
            # Semantic transactions must fail closed rather than silently
            # degrading into an empty/non-cascading state update.
            try:
                payload = json.loads(state_raw)
                if payload.get("semantic_changes"):
                    raise exc
                deltas = payload.get("deltas", [])
                result = merge_deltas(
                    cognition,
                    deltas,
                    source_label=f"agent:{project_id}",
                )
                _record_tool_call(calls, "cognition.merge_state_deltas", payload, result)
                applied += 1
            except Exception as inner_exc:
                _record_tool_call(calls, "cognition.merge_state_deltas", {}, {"error": str(inner_exc)})
        except Exception as exc:
            try:
                safe_payload = json.loads(
                    state_raw
                )
            except Exception:
                safe_payload = {}
            _record_tool_call(
                calls,
                "cognition.merge_state_deltas",
                safe_payload,
                {
                    "error": str(exc)
                },
            )
    return applied
def _persist_drafts(
    metadata_blocks,
    text,
    cognition,
    session_id,
    calls,
):
    """Persist workspace-backed DRAFT_METADATA blocks."""
    drafts = []
    skipped = []
    if not metadata_blocks:
        return drafts, skipped
    for metadata_raw in metadata_blocks:
        try:
            metadata = json.loads(
                metadata_raw
            )
        except Exception as exc:
            _record_tool_call(
                calls,
                "draft.persist",
                {},
                {
                    "error": (
                        "invalid DRAFT_METADATA: "
                        f"{exc}"
                    )
                },
            )
            continue
        if (
            not isinstance(metadata, dict)
            or metadata.get("error")
        ):
            continue
        target_file = metadata.get(
            "target_file"
        )
        # A DRAFT_METADATA without a target is a
        # conversational/record-only draft.
        if not target_file:
            draft = DraftManager(
                cognition
            ).create(
                text,
                metadata,
                session_id,
            )
            drafts.append(draft)
            _record_tool_call(
                calls,
                "draft.persist",
                {
                    "draft_id": draft["id"]
                },
                draft,
            )
            continue
        # A target_file must refer to an actual
        # workspace artifact.
        snapshot = _read_workspace_snapshot(
            cognition,
            target_file,
        )
        if snapshot is None:
            skipped.append(target_file)
            _record_tool_call(
                calls,
                "draft.persist",
                {
                    "target_file": target_file
                },
                {
                    "error": (
                        "no matching workspace file "
                        "found; draft not created"
                    )
                },
            )
            continue
        draft = DraftManager(
            cognition
        ).create(
            snapshot,
            metadata,
            session_id,
        )
        drafts.append(draft)
        _record_tool_call(
            calls,
            "draft.persist",
            {
                "draft_id": draft["id"],
                "target_file": target_file,
            },
            draft,
        )
    return drafts, skipped
_BASE_PROJECT_INSPECTION_TOOLS = (
    "list_project_files",
    "read_project_text",
)

_BASE_PROJECT_MUTATION_TOOLS = (
    *_BASE_PROJECT_INSPECTION_TOOLS,
    "create_project_file",
    "edit_project_text",
    "propose_source_file",
    "propose_source_edit",
)


def _ensure_available_tools(selected, registry, names):
    """Add runtime-required capabilities when they exist in the active policy."""
    available = {str(getattr(t, "name", "")) for t in getattr(registry, "_tools", {}).values()} if isinstance(getattr(registry, "_tools", None), dict) else set()
    for name in names:
        if name in available and name not in selected:
            selected.append(name)


def _successful_project_mutation(calls):
    """Return True only for an actual successful workspace/source proposal mutation."""
    mutation_tools = {
        "create_project_file", "edit_project_text", "create_project_folder",
        "copy_project_item", "copy_to_workspace", "move_project_item", "delete_project_item",
        "propose_source_file", "propose_source_edit",
    }
    for call in calls:
        if call.get("tool") not in mutation_tools:
            continue
        result = call.get("result")
        if isinstance(result, dict) and not result.get("error") and result.get("status") != "error":
            return True
    return False


def _successful_project_read(calls, area=None):
    """True after a real project file has been read successfully.

    ``area`` may be ``source`` or ``workspace``.  When omitted, either area
    counts.  Directory listings, cognition and semantic prefetch do not count.
    """
    for call in calls:
        if call.get("tool") != "read_project_text":
            continue
        args = call.get("args") or {}
        result = call.get("result")
        if area is not None and args.get("area") != area:
            continue
        if args.get("area") not in {"source", "workspace"}:
            continue
        if not isinstance(result, dict):
            continue
        if result.get("error") or result.get("status") == "error":
            continue
        if "content" in result:
            return True
    return False


def _inspection_areas(query):
    """Return the project areas that must be inspected for this request.

    Workspace is mandatory when the user names it.  Source is mandatory for
    source/model/schema/code requests.  If neither is explicit, source remains
    the conservative default for project inspection.
    """
    q = str(query or "").lower()
    areas = []
    if re.search(r"\b(workspace|draft|staged|data\.xml)\b", q):
        areas.append("workspace")
    if re.search(r"\b(source|models?|schema|tables?|classes?|code|implementation|invoice|accruals?)\b", q):
        areas.append("source")
    if not areas:
        areas.append("source")
    return tuple(dict.fromkeys(areas))


def _prime_project_area_context(registry, calls, area):
    """List one real project area before source/workspace-backed model work."""
    args = {"area": area, "relative_path": ""}
    result = _json_safe(registry.call("list_project_files", args))
    _record_tool_call(calls, "list_project_files", args, result)
    return result


def run_agent(context: AgentRunContext):
    """Public agent entry point with optional pluggable execution tracing."""
    trace = context.trace
    with trace_scope(trace):
        if trace is not None:
            trace_event(
                "turn_started",
                project_id=context.project_id,
                session_id=context.session_id,
                turn_id=context.turn_id,
                transcript_messages=len(context.transcript),
                tool_count=len(context.tools),
            )
        try:
            result = _run_agent_impl(context)
        except Exception as exc:
            trace_event("error", stage="agent", error=str(exc), error_type=exc.__class__.__name__)
            trace_event("turn_finished", status="error")
            raise
        answer, calls, drafts = result
        trace_event(
            "turn_finished",
            status="completed",
            answer_chars=len(str(answer or "")),
            tool_calls=len(calls or []),
            drafts=len(drafts or []),
        )
        return result


def _run_agent_impl(context: AgentRunContext):
    """Run the cognitive agent loop.
    Durable fiction/lore changes are represented by STATE_UPDATE and are
    merged directly into cognition. Source-backed implementation changes
    continue to use workspace drafts and approval.
    """
    if not isinstance(context, AgentRunContext):
        raise TypeError("run_agent() requires AgentRunContext")
    transcript = context.transcript
    tools = context.tools
    cognition = context.cognition
    project_id = context.project_id
    session_id = context.session_id
    turn_id = context.turn_id
    active_turn_ids = list(context.active_turn_ids or [])
    on_section = context.on_section
    max_steps = context.max_steps if context.max_steps is not None else MAX_AGENT_STEPS
    LOGGER.info(
        "Agent start project=%s session=%s messages=%d max_steps=%d tools=%d",
        project_id, session_id or "none", len(transcript), max_steps, len(tools),
    )
    registry = ToolRegistry(tools)
    contents = to_provider_contents(
        transcript,
        cognition,
    )
    cycle = (
        CognitiveCycle(
            cognition,
            session_id,
        )
        if cognition is not None
        else None
    )
    # --------------------------------------------------------------
    # Local semantic selection. One local embedding query selects candidate
    # capabilities, cognition, prompt modules and situational instructions.
    # No generative/API routing call is made here.
    # --------------------------------------------------------------
    calls = []
    query = _latest_user_query(transcript)
    trace_event(
        "input_received",
        query=query,
        query_chars=len(query),
        transcript_messages=len(transcript),
    )
    selector = ContextSelector(cognition, tools)
    trace_event("context_selection_started", query=query)
    # Never classify casual chat with a growing phrase dictionary. The model call
    # remains independent; the hybrid cognition retriever decides whether project
    # knowledge is relevant enough to prefetch.
    selection = selector.select(query)
    trace_event(
        "context_selection_finished",
        mode=selection.get("mode"),
        intent=selection.get("intent"),
        intent_score=selection.get("intent_score"),
        selected_tools=selection.get("tool_names") or [],
        cognition_candidates=len(selection.get("candidates") or []),
        prefetched_requests=len((selection.get("prefetched") or {}).get("requests") or []),
    )

    # Cognition mutation and explicit recompilation are execution contracts, not
    # optional model behaviours. Apply them before the conversational model turn.
    # This prevents current cognition from talking the model out of a user correction
    # and prevents fabricated/stale source paths from being passed to the compiler.
    explicit_cognition_mutation = (
        _looks_like_cognition_mutation(query)
        or _asserts_known_entity_state(query, cognition)
        or _edits_known_entity(query, cognition)
    )
    explicit_recompile = _requests_recompile(query)
    cognition_mutation_result = None
    recompile_result = None
    if explicit_cognition_mutation:
        if cognition is None or not cognition.exists():
            cognition_mutation_result = {"status": "error", "error": "cognition store does not exist"}
        else:
            try:
                from ragapp.cognition.merge import apply_semantic_mutation
                change_text = _semantic_change_text(query)
                cognition_mutation_result = apply_semantic_mutation(
                    cognition, [change_text], source_label=f"user:{project_id}"
                )
            except Exception as exc:
                LOGGER.exception("Explicit cognition mutation failed project=%s", project_id)
                cognition_mutation_result = {"status": "error", "error": str(exc)}
        _record_tool_call(
            calls, "cognition.semantic_mutation",
            {"requested_change": _semantic_change_text(query)}, cognition_mutation_result,
        )

    if explicit_recompile and not explicit_cognition_mutation:
        # A semantic mutation already recompiles its supporting authoritative
        # source under the new state. Do not immediately compile the same source
        # a second time for compound requests such as "Ash is 22. recompile".
        # Standalone recompile still resolves the real current /source tree.
        try:
            recompile_result = _json_safe(registry.call("recompile_source", {}))
        except Exception as exc:
            LOGGER.exception("Explicit recompile failed project=%s", project_id)
            recompile_result = {"status": "error", "error": str(exc)}
        _record_tool_call(calls, "recompile_source", {}, recompile_result)
    elif explicit_recompile and explicit_cognition_mutation:
        # "change X and compile it" means: apply the change to cognition. The
        # mutation is surgical and already updated the connected objects; it does
        # not re-extract the unchanged source documents.
        if isinstance(cognition_mutation_result, dict):
            recompile_result = {"status": cognition_mutation_result.get("status") == "applied" and "compiled" or "error",
                                "mode": "surgical_cognition_mutation",
                                "error": cognition_mutation_result.get("error")}

    chat_cognition_result = None
    if _requests_chat_cognition(query) and not explicit_cognition_mutation and not explicit_recompile:
        if cognition is None or not cognition.exists():
            chat_cognition_result = {"status": "error", "error": "cognition store does not exist"}
        else:
            try:
                from ragapp.cognition.merge import import_chat_cognition
                chat_cognition_result = import_chat_cognition(
                    cognition,
                    transcript,
                    session_id=session_id,
                    turn_id=turn_id,
                )
            except Exception as exc:
                LOGGER.exception("Chat cognition import failed project=%s", project_id)
                chat_cognition_result = {"status": "error", "error": str(exc)}
        _record_tool_call(
            calls,
            "cognition.import_chat",
            {"session_id": session_id, "turn_id": turn_id, "message_count": len(transcript)},
            chat_cognition_result,
        )

    # Selection/prefetch was computed before the mutation. Refresh it after a
    # successful mutation so the model cannot be shown stale pre-mutation state.
    if explicit_cognition_mutation and isinstance(cognition_mutation_result, dict) and cognition_mutation_result.get("status") == "applied":
        selection = selector.select(query)

    selected_modules = list(selection.get("modules") or [])
    selected_modules.extend(context.agent_guidance())
    intent = selection.get("intent")
    project_mutation_intent = intent == "project_mutation"
    project_inspection_intent = intent == "project_inspection"
    required_inspection_areas = _inspection_areas(query) if project_inspection_intent else ()
    # Backwards-compatible alias for the existing mutation execution path.
    execution_intent = project_mutation_intent

    if project_inspection_intent:
        selected_modules.append(
            "This turn requires inspection of actual project files. "
            f"Required project areas for this request: {', '.join(required_inspection_areas)}. "
            "Use list_project_files and read_project_text in every required area. "
            "Do not answer from cognition, semantic retrieval, filenames, symbol metadata, or assumptions "
            "when the requested information can be obtained from project files. Read the relevant files "
            "before producing the final answer. For requests that combine workspace input data with source "
            "models/schema, inspect BOTH areas and use the real contents from each."
        )

    if project_mutation_intent:
        selected_modules.append(
            "This turn is an explicit project mutation request. Inspect the existing project with project tools, "
            "make the requested change in /workspace, and create the normal pending source review proposal. "
            "Do not ask the user to paste a project file that can be discovered/read with project tools. "
            "Do not stop at a plan, example, or offer to implement."
        )
    system_instruction = _build_system_instruction(selection.get("instructions"), selected_modules)
    if cognition_mutation_result is not None:
        system_instruction += (
            "\n\nRUNTIME COGNITION MUTATION RESULT (already executed; report only this actual result):\n"
            + json.dumps(_json_safe(cognition_mutation_result), ensure_ascii=False)
        )
    if recompile_result is not None:
        system_instruction += (
            "\n\nRUNTIME RECOMPILE RESULT (already executed against the actual current /source tree; report only this actual result):\n"
            + json.dumps(_json_safe(recompile_result), ensure_ascii=False)
        )
    prefetched = selection.get("prefetched")
    if prefetched and prefetched.get("requests"):
        calls.append({
            "tool": "controller.semantic_prefetch",
            "args": {"query": query, "limit": 4, "max_chars": 4000, "mode": selection.get("mode")},
            "result": _json_safe(prefetched),
        })
        system_instruction += (
            "\n\nRELEVANT CANONICAL COGNITION (locally selected candidates; current project state):\n"
            + json.dumps(prefetched.get("requests", []), ensure_ascii=False)
        )
    allowed_tool_names = list(selection.get("tool_names") or [])
    # Existing-document artifact transformations are an execution contract.
    # Do not expose generic write_pptx/write_pdf tools for these requests: those
    # tools allow the model to invent a condensed artifact. Force the high-level
    # source-preserving transformation tool instead.
    if _looks_like_document_transform(query):
        generic_artifact_writers = {
            "write_pptx", "create_pptx", "write_pdf", "create_pdf",
            "write_docx", "create_docx", "write_html", "create_html",
            "write_markdown", "create_markdown",
        }
        allowed_tool_names[:] = [n for n in allowed_tool_names if n not in generic_artifact_writers]
        _ensure_available_tools(allowed_tool_names, registry, ("transform_document", "read_docx"))
        system_instruction += (
            "\n\nDOCUMENT TRANSFORM: use transform_document; preserve_content=true unless the user explicitly requests summarization/condensation.\n"
        )
    # Deterministic copy path: a copy/duplicate/clone request always gets the
    # one-shot folder-capable tool, regardless of semantic top-k ranking.
    if re.search(r"\b(copy|duplicate|clone)\b", str(query or ""), re.I):
        _ensure_available_tools(allowed_tool_names, registry, ("list_project_files", "copy_to_workspace"))
    # Whole-project undo: always expose the one-shot restore tool (never per-file loops).
    if re.search(r"\b(undo|revert|roll\s?back|rollback|restore|go back)\b", str(query or ""), re.I):
        _ensure_available_tools(allowed_tool_names, registry, ("restore_project_state", "list_project_states"))
    if project_inspection_intent:
        _ensure_available_tools(allowed_tool_names, registry, _BASE_PROJECT_INSPECTION_TOOLS)
    if project_mutation_intent:
        _ensure_available_tools(allowed_tool_names, registry, _BASE_PROJECT_MUTATION_TOOLS)

    # Project discovery is runtime responsibility, not model discretion.
    # Inspection may require /workspace, /source, or both.
    if project_inspection_intent:
        project_trees = {}
        for area in required_inspection_areas:
            try:
                project_trees[area] = _prime_project_area_context(registry, calls, area)
            except Exception as exc:
                LOGGER.exception("Could not inspect project area=%s project=%s", area, project_id)
                project_trees[area] = {"error": str(exc)}

        system_instruction += (
            "\n\nCURRENT PROJECT TREES (runtime-inspected before this turn):\n"
            + json.dumps(project_trees, ensure_ascii=False)
            + "\n\nPROJECT INSPECTION CONTRACT:\n"
            + "- Required areas: " + ", ".join(required_inspection_areas) + ".\n"
            + "- You MUST use read_project_text on relevant files in EVERY required area before answering.\n"
            + "- If workspace contains an input file (for example data.xml), read that actual workspace file.\n"
            + "- If source contains models/schema needed to interpret that input, read those actual source files too.\n"
            + "- Cognition, semantic retrieval, filenames and symbol metadata are navigation aids only.\n"
            + "- Do not ask the user to paste a file that exists in a listed project area.\n"
        )

    if project_mutation_intent:
        try:
            source_tree = _prime_project_area_context(registry, calls, "source")
        except Exception as exc:
            LOGGER.exception("Could not inspect authoritative source tree project=%s", project_id)
            source_tree = {"error": str(exc)}
        if isinstance(source_tree, dict) and not source_tree.get("error"):
            system_instruction += (
                "\n\nCURRENT AUTHORITATIVE SOURCE TREE (runtime-inspected before this turn):\n"
                + json.dumps(source_tree, ensure_ascii=False)
                + "\nUse read_project_text on relevant existing files before editing. "
                  "Stage the completed source change with propose_source_edit/propose_source_file; "
                  "that tool creates the pending Review item."
            )

    def _discover_tools(requirement, limit=6):
        try:
            hits = selector.index.search("tools", requirement, limit=max(1, min(int(limit or 6), 12)), min_score=0.15)
            return {"matches": [{"name": h["id"], "description": h["text"], "score": h["score"]} for h in hits]}
        except Exception as exc:
            return {"error": str(exc)}
    registry.add(Tool(
        "discover_tools",
        "Fallback capability discovery. Use only when the currently exposed tools do not cover the required project operation. Describe the missing capability semantically.",
        {"type": "object", "properties": {"requirement": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["requirement"]},
        _discover_tools,
    ))
    allowed_tool_names.append("discover_tools")
    function_declarations = registry.as_function_declarations(allowed_tool_names)
    trace_event(
        "model_context_ready",
        message_count=len(contents),
        system_chars=len(system_instruction or ""),
        tool_count=len(function_declarations or []),
        tools=[d.get("name") for d in (function_declarations or []) if isinstance(d, dict)],
        context_chars=sum(len(str(m.get("content", ""))) for m in contents if isinstance(m, dict)),
    )
    LOGGER.info(
        "Context selection project=%s mode=%s tools=%d cognition=%d modules=%d instructions=%d",
        project_id,
        selection.get("mode"),
        len(selection.get("tool_names") or []),
        len((prefetched or {}).get("requests") or []),
        len(selection.get("modules") or []),
        len(selection.get("instructions") or []),
    )
    # --------------------------------------------------------------
    # Agent loop
    # --------------------------------------------------------------
    execution_gate_retries = 0
    for step_number in range(max_steps):
        LOGGER.info("Agent model step=%d/%d project=%s", step_number + 1, max_steps, project_id)
        trace_event(
            "model_step_started",
            step=step_number + 1,
            max_steps=max_steps,
            message_count=len(contents),
            tool_count=len(function_declarations or []),
        )
        try:
            step = generate_step(
                contents,
                function_declarations,
                system_instruction=system_instruction,
                store=cognition,
            )
        except EmptyResponseError as exc:
            LOGGER.warning("Agent empty response project=%s step=%d error=%s", project_id, step_number + 1, exc)
            return (
                f"⚠️ {exc}",
                calls,
                [],
            )
        except Exception:
            LOGGER.exception("Agent model request failed project=%s step=%d", project_id, step_number + 1)
            raise
        trace_event(
            "model_step_finished",
            step=step_number + 1,
            function_calls=len(step.get("function_calls") or []),
            response_chars=len(str(step.get("text") or "")),
            model=step.get("model"),
            usage=step.get("usage"),
        )
        # ----------------------------------------------------------
        # Model finished
        # ----------------------------------------------------------
        if not step["function_calls"]:
            # Explicit cognition/recompile commands may only be reported as successful
            # when the runtime operation above actually succeeded.
            if explicit_cognition_mutation and (not isinstance(cognition_mutation_result, dict) or cognition_mutation_result.get("status") not in {"applied", "ok", "success"}):
                error = (cognition_mutation_result or {}).get("error") if isinstance(cognition_mutation_result, dict) else "unknown error"
                return (f"I could not apply the requested cognition update: {error}", calls, [])
            if explicit_recompile and (not isinstance(recompile_result, dict) or recompile_result.get("status") not in {"compiled", "ok", "success"}):
                error = (recompile_result or {}).get("error") if isinstance(recompile_result, dict) else "unknown error"
                return (f"I could not recompile the project cognition: {error}", calls, [])
            # A project inspection must consume real authoritative source before
            # the model may finish. This prevents partial cognition/metadata from
            # being presented as if it were the complete project schema/content.
            if project_inspection_intent:
                missing_areas = [
                    area for area in required_inspection_areas
                    if not _successful_project_read(calls, area)
                ]
                if missing_areas:
                    if execution_gate_retries < 3:
                        execution_gate_retries += 1
                        system_instruction += (
                            "\n\nPROJECT INSPECTION GATE: You attempted to finish before reading all required "
                            "project areas. Missing successful read_project_text calls for: "
                            + ", ".join(missing_areas)
                            + ". Read the relevant files in those areas now. If an input file such as data.xml "
                              "is in workspace, read it from area='workspace'. If models/schema are in source, "
                              "read them from area='source'. Then complete the user's original request directly."
                        )
                        LOGGER.warning(
                            "Blocked incomplete project inspection project=%s step=%d retry=%d missing=%s",
                            project_id, step_number + 1, execution_gate_retries, missing_areas,
                        )
                        continue
                    return (
                        "I could not read all project areas required for this request: "
                        + ", ".join(missing_areas),
                        calls,
                        [],
                    )

            # A project mutation request is executable work. The base runtime does
            # not allow the first model turn to downgrade it into advice, a plan,
            # or a request for files that the project tools can inspect directly.
            if execution_intent and not _successful_project_mutation(calls):
                if execution_gate_retries < 2:
                    execution_gate_retries += 1
                    system_instruction += (
                        "\n\nEXECUTION GATE: The requested project mutation has not been performed yet. "
                        "Do not answer conversationally. Use list_project_files/read_project_text as needed, "
                        "then write the change to workspace and use propose_source_edit/propose_source_file so "
                        "the user receives it in Review."
                    )
                    LOGGER.warning(
                        "Blocked non-executing mutation response project=%s step=%d retry=%d",
                        project_id, step_number + 1, execution_gate_retries,
                    )
                    continue
                return (
                    "I could not execute the requested project change because the mutation tools were not successfully used.",
                    calls,
                    [],
                )
            text = step["text"] or ""
            metadata_blocks, text = (
                _extract_blocks(
                    text,
                    "DRAFT_METADATA",
                )
            )
            state_blocks, text = (
                _extract_blocks(
                    text,
                    "STATE_UPDATE",
                )
            )
            # ------------------------------------------------------
            # Durable cognition updates
            #
            # This is the path for fiction/lore changes such as:
            #
            # "Change Thaleryx to Tier 4."
            #
            # No source DOCX needs to be rewritten merely because
            # cognition changed.
            # ------------------------------------------------------
            state_update_count = (
                _persist_state_updates(
                    state_blocks,
                    cognition,
                    project_id,
                    calls,
                )
            )
            # ------------------------------------------------------
            # Workspace-backed drafts
            #
            # This remains the path for implementation/source-file
            # changes.
            # ------------------------------------------------------
            drafts, skipped = _persist_drafts(
                metadata_blocks,
                text,
                cognition,
                session_id,
                calls,
            )
            if skipped:
                text += (
                    "\n\n---\n"
                    "⚠️ No draft was created for: "
                    + ", ".join(skipped)
                    + ". These files were described "
                    "but never actually written to the "
                    "workspace with a file tool."
                )
            if cycle:
                cycle.advance(
                    "persist",
                    drafts=len(drafts),
                    state_updates=(
                        state_update_count
                    ),
                )
                try:
                    SessionMemory(cognition).distill(
                        session_id or "unknown",
                        transcript + [{"role": "assistant", "content": text, "turn_id": turn_id}],
                        turn_id=turn_id,
                        active_turn_ids=active_turn_ids or None,
                    )
                except Exception:
                    pass
            LOGGER.info("Agent completed project=%s step=%d tool_calls=%d drafts=%d", project_id, step_number + 1, len(calls), len(drafts))
            return (
                text,
                calls,
                drafts,
            )
        # ----------------------------------------------------------
        # Model requested normal tools
        # ----------------------------------------------------------
        model_content = step.get(
            "model_content"
        )
        if model_content:
            contents.append(
                model_content
            )
        # Execute every requested tool and preserve
        # its actual identity in calls[].
        for call in step["function_calls"]:
            tool_name = call.get(
                "name",
                "",
            )
            tool_args = call.get(
                "args",
                {},
            )
            LOGGER.info("Tool call project=%s step=%d tool=%s arg_keys=%s", project_id, step_number + 1, tool_name, sorted(tool_args.keys()) if isinstance(tool_args, dict) else [])
            trace_event(
                "tool_started",
                step=step_number + 1,
                tool=tool_name,
                arguments=_json_safe(tool_args),
            )
            if call.get("args_error"):
                # The model sent malformed arguments; report it back instead
                # of executing the tool with empty/default arguments.
                result = {
                    "error": call["args_error"],
                    "tool": tool_name,
                }
            elif tool_name == "restore_file_revision" and sum(1 for c in calls if c.get("tool") == "restore_file_revision") >= 3:
                # Loop breaker: the model is undoing file-by-file. Point it at the single-call tool.
                result = {
                    "error": "Too many single-file restores in one run. Stop. Use restore_project_state ONCE "
                             "(target='last_compile' or a commit id) to restore the whole project.",
                    "tool": tool_name,
                }
            else:
                try:
                    result = registry.call(
                        tool_name,
                        tool_args,
                    )
                except Exception as exc:
                    result = {
                        "error": str(exc),
                        "tool": tool_name,
                    }
            safe_result = _json_safe(
                result
            )
            if tool_name == "discover_tools" and isinstance(safe_result, dict):
                for match in safe_result.get("matches") or []:
                    name = str(match.get("name") or "")
                    if name and name not in allowed_tool_names:
                        allowed_tool_names.append(name)
                function_declarations = registry.as_function_declarations(allowed_tool_names)
            # Retrieval continuation is deliberately NOT auto-consumed.  A bounded
            # first page is returned to the model; another page is fetched only if
            # the model explicitly determines the supplied evidence is insufficient.
            if isinstance(safe_result, dict) and (safe_result.get("error") or safe_result.get("status") == "error"):
                LOGGER.warning("Tool failed project=%s step=%d tool=%s error=%s", project_id, step_number + 1, tool_name, safe_result.get("error", "unknown error"))
            else:
                LOGGER.info("Tool completed project=%s step=%d tool=%s", project_id, step_number + 1, tool_name)
            _record_tool_call(
                calls,
                tool_name,
                tool_args,
                safe_result,
            )
            if (
                tool_name == "restore_project_state"
                and isinstance(safe_result, dict)
                and not safe_result.get("error")
                and safe_result.get("status") in {"restored", "unchanged"}
            ):
                # Terminal tool: report deterministically and STOP. No further model turns.
                if safe_result["status"] == "unchanged":
                    final = (f"The project already matches {safe_result['restored_to'][:10]} "
                             f"({safe_result['restored_to_message']}); nothing to undo.")
                else:
                    final = (f"Restored the whole project to {safe_result['restored_to'][:10]} "
                             f"({safe_result['restored_to_message']}). {safe_result['files_affected']} file(s) affected. "
                             f"{safe_result['undo_hint']}")
                LOGGER.info("Agent completed via terminal restore project=%s", project_id)
                return (final, calls, [])
            model_result = safe_result
            # A page/range requested for display is rendered directly from
            # the local PDF. The page text never enters the model context.
            if (
                tool_name in _DIRECT_PDF_PAGE_TOOLS
                and isinstance(safe_result, dict)
                and isinstance(safe_result.get("pages"), list)
            ):
                for page_item in safe_result["pages"]:
                    if not isinstance(page_item, dict):
                        continue
                    page_number = page_item.get("page")
                    page_text = page_item.get("text") or ""
                    if page_text:
                        _emit_source_chunk(
                            on_section,
                            {
                                "title": f"{safe_result.get('path', 'PDF')} - page {page_number}",
                                "path": safe_result.get("path"),
                                "text": page_text,
                            },
                            page_number,
                        )
                model_result = {
                    "path": safe_result.get("path"),
                    "page_count": safe_result.get("page_count"),
                    "pages_delivered": [
                        item.get("page")
                        for item in safe_result.get("pages", [])
                        if isinstance(item, dict)
                    ],
                    "delivered_to_user": True,
                    "message": "Requested PDF page(s) were rendered directly to the user; do not repeat their text.",
                }

            # Long source documents are paged locally. Each piece is rendered
            # immediately, while the model receives delivery metadata only.
            elif (
                tool_name in _PAGED_SOURCE_TOOLS
                and isinstance(safe_result, dict)
                and safe_result.get("text")
            ):
                chunks_delivered = 1
                _emit_source_chunk(on_section, safe_result, chunks_delivered)
                current_result = safe_result
                while (
                    current_result.get("next_offset") is not None
                    and chunks_delivered < _MAX_AUTO_SOURCE_CHUNKS
                ):
                    continuation_args = dict(tool_args)
                    continuation_args["offset"] = current_result["next_offset"]
                    LOGGER.info(
                        "Source continuation project=%s tool=%s chunk=%d offset=%d",
                        project_id, tool_name, chunks_delivered + 1,
                        continuation_args["offset"],
                    )
                    continuation_result = _json_safe(registry.call(tool_name, continuation_args))
                    _record_tool_call(calls, tool_name, continuation_args, continuation_result)
                    if not isinstance(continuation_result, dict) or continuation_result.get("error"):
                        LOGGER.warning("Source continuation failed project=%s tool=%s", project_id, tool_name)
                        break
                    chunks_delivered += 1
                    _emit_source_chunk(on_section, continuation_result, chunks_delivered)
                    current_result = continuation_result
                model_result = {
                    "path": safe_result.get("path"),
                    "title": safe_result.get("title"),
                    "delivered_to_user": True,
                    "chunks_delivered": chunks_delivered,
                    "complete": current_result.get("complete", False),
                    "next_offset": current_result.get("next_offset"),
                    "message": "Source text was streamed directly to the user; do not repeat it.",
                }
            # Feed the actual tool result back into
            # the model, preserving the provider's
            # function-call/result protocol.
            contents.append(
                build_function_response_content(
                    tool_name,
                    model_result,
                    call.get("id"),
                    store=cognition,
                )
            )
    # --------------------------------------------------------------
    # Execution limit
    # --------------------------------------------------------------
    LOGGER.warning("Agent execution limit reached project=%s tool_calls=%d max_steps=%d", project_id, len(calls), max_steps)
    return (
        "I could not complete the workflow within "
        "the configured execution limit.",
        calls,
        [],
    )