"""Deterministic request routing for prompt/tool minimization.

This module never calls an LLM. It selects only the capabilities that are
plausibly required for the current user request. Unknown/ambiguous requests
fall back to a safe general route rather than paying for a second model call.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import re

@dataclass(frozen=True)
class RequestRoute:
    capabilities: frozenset[str] = field(default_factory=frozenset)
    reason: str = "general"

    def has(self, capability: str) -> bool:
        return capability in self.capabilities


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def route_request(query: str) -> RequestRoute:
    q = (query or "").strip().lower()
    if not q:
        return RequestRoute(frozenset({"base"}), "empty")

    caps = {"base"}
    if q in {"hi", "hello", "hey", "hiya", "yo", "sup", "thanks", "thank you", "ok", "okay", "good morning", "good afternoon", "good evening"}:
        return RequestRoute(frozenset(caps), "simple-chat")

    cognition = _has(q,
        r"\b(entit(?:y|ies)|cognition|relationship|timeline|event|state|fact|knowledge|canon|lore|concept|definition|provenance|contradiction|impact)\b",
        r"\b(who|what|where|when)\s+(?:is|are|was|were)\b")
    temporal = _has(q, r"\b(current|latest|previous|histor(?:y|ical)|timeline|before|after|chapter|session|turn|flashback|era|as of)\b")
    mermaid = _has(q, r"\bmermaid\b", r"\b(flowchart|sequence diagram|class diagram)\b")
    vcs = _has(q, r"\b(git|commit|revision|version history|restore|rollback|diff|checkpoint)\b")
    compile_task = _has(q, r"\b(recompile|compile|compilation|ingest|reingest)\b")
    source_mutation = _has(q, r"\b(edit|change|modify|update|replace|append|delete|remove|create|write|rename|move)\b.*\b(source|file|code|\.py|\.js|\.ts|\.json|\.ya?ml|\.toml)\b",
                           r"\b(source|file|code|\.py|\.js|\.ts|\.json|\.ya?ml|\.toml)\b.*\b(edit|change|modify|update|replace|append|delete|remove|create|write|rename|move)\b")
    files = _has(q, r"\b(file|folder|workspace|source|document|docx|pdf|xml|pptx|xlsx|excel|powerpoint|binary|csv|markdown|\.md|\.txt|\.py)\b")
    agent_creation = _has(q, r"\b(create|build|define|configure|edit|update|delete|list|show)\b.*\bagent\b", r"\bagent\s+(creator|creation|manager|definition)\b")
    workflow = _has(q, r"\bworkflow\b", r"\b(deterministic|inference)\s+step\b")

    if cognition: caps.add("cognition")
    if temporal: caps.update({"cognition", "temporal"})
    if mermaid: caps.update({"mermaid", "cognition"})
    if vcs: caps.update({"vcs", "files"})
    if compile_task: caps.update({"compilation", "cognition"})
    if files: caps.add("files")
    if source_mutation: caps.update({"files", "source_mutation", "vcs"})
    if agent_creation: caps.add("agent_creation")
    if workflow: caps.add("workflow")

    # Content questions in a compiled project should prefer cognition even when
    # they do not contain an explicit cognition noun.
    if _has(q, r"\b(explain|summari[sz]e|describe|tell me about|list)\b") and not (files or vcs or agent_creation or workflow):
        caps.add("cognition")

    # Unknown requests get a compact general route, not the full artifact suite.
    if caps == {"base"}:
        caps.add("cognition")
        reason = "general-cognition"
    else:
        reason = "+".join(sorted(caps - {"base"}))
    return RequestRoute(frozenset(caps), reason)


_COGNITION_TOOLS = {
    "search_cognition_metadata", "get_cognition_index", "request_cognition_context",
    "get_entity_metadata", "get_state_map", "get_ledger", "load_entities",
    "record_fact", "record_evidence", "record_hypothesis", "record_decision",
    "record_dependency", "record_change", "get_contradictions", "analyze_impact",
    "validate_cognition", "get_world_model", "distill_session",
}
_VCS_TOOLS = {"list_file_history", "show_file_revision", "diff_file_revisions", "restore_file_revision", "create_vcs_checkpoint"}
_PROJECT_READ = {"list_project_files", "read_project_text", "list_workspace_files", "copy_workspace_file"}
_PROJECT_WRITE = {"create_project_folder", "create_project_file", "copy_project_item", "move_project_item", "delete_project_item", "edit_project_text", "propose_source_file", "propose_source_edit"}
_FORMAT_PREFIXES = ("read_", "write_", "edit_", "delete_", "copy_pdf_pages")


def allowed_tool_names(route: RequestRoute, available_names) -> set[str]:
    names = set(available_names)
    allowed: set[str] = set()
    if route.has("cognition") or route.has("compilation"):
        allowed |= _COGNITION_TOOLS
    if route.has("vcs"):
        allowed |= _VCS_TOOLS
    if route.has("files"):
        allowed |= _PROJECT_READ
        # Format-specific reads are useful for explicit file work.
        allowed |= {n for n in names if n.startswith("read_")}
    if route.has("source_mutation"):
        allowed |= _PROJECT_WRITE
        allowed |= {n for n in names if n.startswith(("write_", "edit_", "delete_")) or n == "copy_pdf_pages"}
    if route.has("agent_creation"):
        allowed |= {n for n in names if "agent" in n}
    if route.has("workflow"):
        allowed |= {n for n in names if "workflow" in n}
    return allowed & names
