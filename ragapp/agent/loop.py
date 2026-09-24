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
_SIMPLE_CHAT = {
    "hi",
    "hello",
    "hey",
    "hiya",
    "yo",
    "sup",
    "good morning",
    "good afternoon",
    "good evening",
    "thanks",
    "thank you",
    "ok",
    "okay",
}
def _is_simple_chat(transcript):
    if not transcript:
        return False
    text = str(
        transcript[-1].get("content", "")
    ).strip().lower()
    return text in _SIMPLE_CHAT

def _latest_user_query(transcript):
    for message in reversed(transcript or []):
        if str(message.get("role", "")).lower() == "user":
            return str(message.get("content", "") or "").strip()
    return ""


def _prefetch_cognition(cognition, transcript, max_chars=8000, limit=8):
    """Deterministic lexical/canonical prefetch; no LLM inference is used here.

    CognitionStore.search_cognition_metadata performs the local candidate
    search.  The LLM receives a bounded compact projection and may use tools to
    refine it.  This guarantees consultation of current canonical state without
    dumping the entire cognition store into the prompt.
    """
    if cognition is None or not cognition.exists() or _is_simple_chat(transcript):
        return None
    query = _latest_user_query(transcript)
    if not query:
        return None
    try:
        retrieval = RetrievalStore(cognition)
        return retrieval.retrieve_for_query(query, max_chars=max_chars, limit=limit, detail="compact")
    except Exception:
        LOGGER.exception("Canonical prefetch failed")
        return None
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
def _build_system_instruction(active_instructions):
    """Build the actual system instruction sent to the provider."""
    system_instruction = COGNITIVE_AGENT_PROMPT + """

TEMPORAL COGNITION RETRIEVAL PROTOCOL:
- Treat narrative/document order and story-world time as separate axes. Never use one as a substitute for the other.
- For present/current questions, retrieve current state (timeline=latest) unless the user explicitly specifies another point.
- For an explicit chapter/session/turn or other document-order reference, request narrative_position/as_of on the narrative axis.
- For an explicit in-world date, age, era, chapter-in-the-world, or flashback point, request story_time/as_of on the story axis.
- A flashback changes the requested story-time state; do not assume the surrounding chapter's current state applies.
- If the temporal reference is ambiguous or cannot be represented precisely, do not guess. Ask for clarification or retrieve without temporal resolution only when the question does not depend on historical state.
- When requesting historical entity state, include the relevant attributes when known and use request_cognition_context rather than relying on metadata summaries.
- Do not invent temporal coordinates merely to make a retrieval request resolvable.
"""
    if not active_instructions:
        return system_instruction
    lines = []
    for instruction in active_instructions:
        if isinstance(instruction, dict):
            content = instruction.get(
                "content",
                "",
            )
        else:
            content = str(instruction)
        content = str(content).strip()
        if content:
            lines.append(f"- {content}")
    if lines:
        system_instruction += (
            "\n\n"
            "PERSISTENT PROJECT INSTRUCTIONS:\n"
            "The following instructions are persistent "
            "project-level instructions. Follow them when "
            "responding and using tools.\n"
            + "\n".join(lines)
        )
    return system_instruction
def _record_tool_call(
    calls,
    name,
    args,
    result,
):
    """Record the actual tool call for the UI/API."""
    calls.append(
        {
            "tool": str(name),
            "args": _json_safe(args),
            "result": _json_safe(result),
        }
    )
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
            if not isinstance(deltas, list):
                raise ValueError(
                    "STATE_UPDATE.deltas must be an array."
                )
            result = merge_deltas(
                cognition,
                deltas,
                source_label=(
                    f"agent:{project_id}"
                ),
                events=events,
            )
            _record_tool_call(
                calls,
                "cognition.merge_state_deltas",
                payload,
                result,
            )
            applied += 1
        except TypeError:
            # Backward compatibility for merge_deltas versions
            # that do not accept events=.
            try:
                payload = json.loads(state_raw)
                deltas = payload.get(
                    "deltas",
                    [],
                )
                result = merge_deltas(
                    cognition,
                    deltas,
                    source_label=(
                        f"agent:{project_id}"
                    ),
                )
                _record_tool_call(
                    calls,
                    "cognition.merge_state_deltas",
                    payload,
                    result,
                )
                applied += 1
            except Exception as exc:
                _record_tool_call(
                    calls,
                    "cognition.merge_state_deltas",
                    {},
                    {
                        "error": str(exc)
                    },
                )
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
def run_agent(
    transcript,
    tools,
    cognition,
    project_id,
    max_steps=MAX_AGENT_STEPS,
    session_id=None,
    on_section=None,
):
    """Run the cognitive agent loop.
    Durable fiction/lore changes are represented by STATE_UPDATE and are
    merged directly into cognition. Source-backed implementation changes
    continue to use workspace drafts and approval.
    """
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
    # Persistent instructions remain part of the system instruction.
    # They are small and are not source retrieval.
    # --------------------------------------------------------------
    active_instructions = _load_persistent_instructions(cognition)
    system_instruction = _build_system_instruction(active_instructions)
    # --------------------------------------------------------------
    # Cognition-first retrieval.  Candidate discovery is deterministic local
    # search, not model inference.  Only a bounded compact projection is
    # injected; the model can refine/broaden it with cognition tools.
    # --------------------------------------------------------------
    calls = []
    prefetched = _prefetch_cognition(cognition, transcript)
    if prefetched and prefetched.get("requests"):
        calls.append({
            "tool": "controller.prefetch_cognition",
            "args": {"query": prefetched.get("query"), "limit": 8, "max_chars": 8000},
            "result": _json_safe(prefetched),
        })
        system_instruction += (
            "\n\nCONTROLLER-PREFETCHED CANONICAL COGNITION "
            "(current authoritative project state; use this before old transcript claims):\n"
            + json.dumps(prefetched.get("requests", []), ensure_ascii=False)
        )
    # --------------------------------------------------------------
    # Tool declarations
    # --------------------------------------------------------------
    if _is_simple_chat(transcript):
        function_declarations = []
    else:
        function_declarations = (
            registry.as_function_declarations()
        )
    # --------------------------------------------------------------
    # Agent loop
    # --------------------------------------------------------------
    for step_number in range(max_steps):
        LOGGER.info("Agent model step=%d/%d project=%s", step_number + 1, max_steps, project_id)
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
        # ----------------------------------------------------------
        # Model finished
        # ----------------------------------------------------------
        if not step["function_calls"]:
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
                    SessionMemory(
                        cognition
                    ).distill(
                        session_id or "unknown",
                        transcript
                        + [
                            {
                                "role": "assistant",
                                "content": text,
                            }
                        ],
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
            if call.get("args_error"):
                # The model sent malformed arguments; report it back instead
                # of executing the tool with empty/default arguments.
                result = {
                    "error": call["args_error"],
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
            # Canonical retrieval continuation is controller-owned.  Consume
            # resumable requests deterministically, with duplicate/round caps,
            # before exposing the result to the model.
            if tool_name == "request_cognition_context" and isinstance(safe_result, dict):
                pending = list(safe_result.get("next_requests") or [])
                seen_pending = set()
                continuation_rounds = 0
                while pending and continuation_rounds < 32:
                    follow = pending.pop(0)
                    key = json.dumps(follow, ensure_ascii=False, sort_keys=True, default=str)
                    if key in seen_pending:
                        continue
                    seen_pending.add(key)
                    continuation_rounds += 1
                    follow_args = {
                        "requests": [follow],
                        "max_chars": int(tool_args.get("max_chars", 12000) or 12000),
                    }
                    follow_result = _json_safe(registry.call(tool_name, follow_args))
                    _record_tool_call(calls, tool_name, follow_args, follow_result)
                    if not isinstance(follow_result, dict) or follow_result.get("error"):
                        break
                    safe_result.setdefault("requests", []).extend(follow_result.get("requests", []))
                    pending.extend(follow_result.get("next_requests") or [])
                safe_result.pop("next_requests", None)
                safe_result["continuations_consumed"] = continuation_rounds
                safe_result["continuation_complete"] = not pending
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
