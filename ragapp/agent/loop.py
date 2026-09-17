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
    system_instruction = COGNITIVE_AGENT_PROMPT
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
    registry = ToolRegistry(tools)
    contents = to_provider_contents(
        transcript,
        cognition,
    )
    retriever = (
        RetrievalStore(cognition)
        if cognition is not None
        else None
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
    # Persistent project briefing
    # --------------------------------------------------------------
    if (
        cognition is not None
        and cognition.exists()
    ):
        briefing = cognition.briefing(
            transcript[-1]["content"]
            if transcript
            else ""
        )
    else:
        briefing = {
            "project": {
                "project_id": project_id
            },
            "selected_entities": {},
            "selected_relationships": {},
        }
    # --------------------------------------------------------------
    # Persistent instructions
    # --------------------------------------------------------------
    active_instructions = (
        _load_persistent_instructions(
            cognition
        )
    )
    system_instruction = (
        _build_system_instruction(
            active_instructions
        )
    )
    # --------------------------------------------------------------
    # Cognitive cycle briefing
    # --------------------------------------------------------------
    if cycle:
        briefing["cognitive_cycle"] = (
            cycle.briefing(
                transcript[-1]["content"]
                if transcript
                else ""
            )
        )
    # --------------------------------------------------------------
    # Initial local cognition retrieval
    # --------------------------------------------------------------
    if (
        cognition is not None
        and cognition.exists()
        and transcript
        and retriever is not None
    ):
        try:
            initial_context = (
                retriever.retrieve_for_query(
                    transcript[-1]["content"],
                    max_chars=min(
                        12000,
                        MAX_CONTEXT_CHARS,
                    ),
                )
            )
        except Exception:
            initial_context = {
                "requests": [],
                "relationships": [],
                "used_chars": 0,
                "truncated": False,
            }
    else:
        initial_context = {
            "requests": [],
            "relationships": [],
            "used_chars": 0,
            "truncated": False,
        }
    # --------------------------------------------------------------
    # Inject cognition context
    # --------------------------------------------------------------
    context_payload = (
        "PERSISTENT PROJECT CONTEXT:\n"
        + json.dumps(
            briefing,
            ensure_ascii=False,
        )
        + "\nRELEVANT COGNITIVE CONTEXT "
        "(retrieved locally; no API call):\n"
        + json.dumps(
            initial_context,
            ensure_ascii=False,
        )
    )
    context_contents = to_provider_contents(
        [
            {
                "role": "user",
                "content": context_payload,
            }
        ],
        cognition,
    )
    if context_contents:
        contents.insert(
            0,
            context_contents[0],
        )
    calls = []
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
        try:
            step = generate_step(
                contents,
                function_declarations,
                system_instruction=system_instruction,
                store=cognition,
            )
        except EmptyResponseError as exc:
            return (
                f"⚠️ {exc}",
                calls,
                [],
            )
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
            _record_tool_call(
                calls,
                tool_name,
                tool_args,
                safe_result,
            )
            # Feed the actual tool result back into
            # the model, preserving the provider's
            # function-call/result protocol.
            contents.append(
                build_function_response_content(
                    tool_name,
                    safe_result,
                    call.get("id"),
                    store=cognition,
                )
            )
    # --------------------------------------------------------------
    # Execution limit
    # --------------------------------------------------------------
    return (
        "I could not complete the workflow within "
        "the configured execution limit.",
        calls,
        [],
    )