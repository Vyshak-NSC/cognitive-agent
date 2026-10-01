"""Semantic, local pre-call context selection.

One local query embedding selects candidate tools, cognition and situational
instructions.  The main LLM remains the only generative semantic reasoner.
"""
from __future__ import annotations

import json
import logging
import re

from ragapp.core.semantic_index import SemanticIndex
from ragapp.core.retrieval import RetrievalStore, cognition_index_records
from ragapp.core.instructions import InstructionStore
from ragapp.agent.trace import trace_event

LOGGER = logging.getLogger("ragapp.agent.context_selector")

# These are retrieval records, not globally injected prompts.
INTENT_RECORDS = {
    "project_mutation": {
        "description": (
            "The user is instructing the system to implement, add, change, fix, remove, "
            "rewrite, configure, or otherwise modify project files or application behavior now. "
            "This is an execution request, not a request for an explanation or implementation plan."
        ),
    },
    "project_inspection": {
        "description": (
            "The user is asking to inspect, read, find, explain, review, or analyze existing "
            "project files without requesting that the project be changed."
        ),
    },
    "cognition_mutation": {
        "description": (
            "The user is explicitly changing, correcting, updating, deleting, or asserting new canonical "
            "project cognition such as an entity attribute, relationship, event, fact, world state, or canon. "
            "This changes persistent cognition rather than project files."
        ),
    },
    "conversation": {
        "description": (
            "The user is asking a general question, discussing an idea, or requesting prose that "
            "does not require changing project files."
        ),
    },
    "document_transform": {
        "description": (
            "The user is transforming an existing document into PDF, PPTX/PowerPoint, DOCX, HTML, "
            "or Markdown while preserving source content unless explicit summarization or condensation "
            "is requested."
        ),
    },
}

PROMPT_MODULES = {
    "knowledge_lifecycle": {
        "description": "Investigation, debugging, research, requirements analysis, architecture choices, implementation work, or any task that can create durable evidence, hypotheses, decisions, changes, dependencies, or unresolved questions.",
        "text": "Maintain the durable project knowledge lifecycle when it materially applies. Record concrete observations as evidence; testable explanations as hypotheses; confirmed durable conclusions as facts; explicit choices as decisions; material dependencies as dependencies; unresolved material unknowns as open questions; and implementation outcomes as changes. Update existing records when their status changes instead of creating duplicates. Do not manufacture records for casual conversation or unsupported guesses.",
    },
    "cognition": {
        "description": "Questions requiring persistent project knowledge, entities, relationships, events, state, chronology, provenance or derived cognition.",
        "text": "Use canonical cognition as project state. Prefer retrieved cognition over old transcript claims. Retrieve more cognition only when the supplied evidence is insufficient.",
    },
    "source_edit": {
        "description": "Create or edit authoritative source files, implementation files, code, configuration, or documents through draft and approval.",
        "text": "Authoritative source mutations must use the source proposal/review path. Do not claim a source change succeeded without tool evidence.",
    },
    "workspace": {
        "description": "Create, read, edit, move, copy or delete temporary workspace files and artifacts.",
        "text": "Workspace operations are temporary/non-authoritative unless promoted through the source review path.",
    },
    "vcs": {
        "description": "Inspect project file history, commits, diffs, historical content, or restore a historical revision.",
        "text": "VCS operations apply to the project repository. Restoration creates a new revision; do not rewrite history.",
    },
    "documents": {
        "description": "Read, transform, or generate PDF DOCX PPTX PowerPoint presentations and other structured documents.",
        "text": "Existing document -> PDF/PPTX/PowerPoint/presentation is TRANSFORM unless the user asks to summarize or condense. Use transform_document with preserve_content=true. Do not substitute a generic authoring tool or a summary.",
    },
    "mermaid": {
        "description": "Create Mermaid diagrams, flowcharts, relationship diagrams, architecture diagrams or visual graphs.",
        "text": "For Mermaid, return one syntactically complete Mermaid fenced block. Prefer simple flowchart syntax and balanced subgraphs. Always quote node labels, for example A[\"Invoice Header\"]. Never put \\n in a Mermaid label; use <br/> for a line break, for example A[\"HeaderRowData<br/>(header)\"].",
    },
}



def _tool_records(tools):
    return [
        {
            "id": tool.name,
            "text": f"{tool.name.replace('_', ' ')}. {tool.description}",
            "metadata": {"name": tool.name},
        }
        for tool in tools
    ]


def _cognition_records(store):
    # Shared with RetrievalStore so both writers of the "cognition" namespace
    # embed identical text (they previously diverged and overwrote each other).
    return cognition_index_records(store)


def _instruction_records(instructions):
    out = []
    for item in instructions:
        if item.get("scope") == "system":
            continue
        content = str(item.get("content") or "").strip()
        if content:
            out.append({"id": str(item.get("id")), "text": content, "metadata": item})
    return out


def _intent_records():
    return [
        {"id": key, "text": value["description"], "metadata": {"key": key}}
        for key, value in INTENT_RECORDS.items()
    ]


def _module_records():
    return [
        {"id": key, "text": value["description"], "metadata": {"key": key}}
        for key, value in PROMPT_MODULES.items()
    ]


def _looks_like_project_mutation(query: str) -> bool:
    """Cheap execution-intent backstop for imperative project changes.

    Semantic routing remains the primary classifier, but an imperative such as
    "add a box on the top left" must not be downgraded to conversation merely
    because it omits words like "file" or "code".
    """
    q = str(query or "").strip().lower()
    if not q:
        return False
    action = re.search(
        r"\b(add|implement|create|make|build|change|modify|update|edit|fix|remove|delete|"
        r"replace|move|rename|wire|connect|integrate|enable|disable|style|render|show|display)\b",
        q,
    )
    if not action:
        return False
    # Strong project/UI/code objects. This intentionally includes visual feature
    # nouns because users normally describe the desired result, not its filename.
    target = re.search(
        r"\b(project|source|code|file|app|page|screen|ui|interface|button|box|panel|"
        r"dropdown|menu|sidebar|header|footer|camera|control|scene|canvas|component|"
        r"function|class|method|api|route|endpoint|database|schema|config|setting|"
        r"html|css|javascript|typescript|python|three\.?js|react|streamlit)\b",
        q,
    )
    return bool(target)


def _looks_like_project_inspection(query: str) -> bool:
    """Deterministic backstop for questions that require reading project source.

    Semantic routing is still useful for capability selection, but source-backed
    questions must not be downgraded to ordinary conversation just because the
    query is terse (for example, "get full schema of invoice").
    """
    q = str(query or "").strip().lower()
    if not q:
        return False

    inspection = re.search(
        r"\b(inspect|read|show|find|locate|explain|review|analy[sz]e|check|trace|list|get|"
        r"extract|derive|determine|schema|structure|fields?|columns?|models?|classes?|"
        r"methods?|functions?|implementation|source)\b",
        q,
    )
    project_target = re.search(
        r"\b(project|source|code|codebase|repo|repository|file|files|folder|package|"
        r"models?|classes?|schema|table|tables|implementation|config|configuration|"
        r"api|route|endpoint|database|invoice|accruals?)\b",
        q,
    )
    return bool(inspection and project_target)


def _looks_like_cognition_mutation(query: str) -> bool:
    """Deterministic backstop for explicit canonical/cognition state changes."""
    q = str(query or "").strip().lower()
    if not q:
        return False
    # Explicit requests to mutate the persistent semantic state.
    cognition_target = re.search(
        r"\b(cognition|canon|canonical|world state|project state|state|fact|relationship|entity|knowledge)\b",
        q,
    )
    mutation = re.search(
        r"\b(update|change|correct|set|make|replace|remove|delete|revise|apply|persist|remember)\b",
        q,
    )
    return bool(cognition_target and mutation)


def _requests_recompile(query: str) -> bool:
    q = str(query or "").strip().lower()
    return bool(re.search(r"\b(re-?compil(?:e|ed|es|ing)|compil(?:e|ed|es|ing)|rebuil[dt]|re-?ingest(?:ed)?)\b", q))


_GENERIC_ARTIFACT_WRITERS = {
    "write_pptx", "create_pptx", "write_pdf", "create_pdf",
    "write_docx", "create_docx", "write_html", "create_html",
    "write_markdown", "create_markdown",
}


def _looks_like_document_transform(query: str) -> bool:
    """Deterministic backstop for existing-document artifact transformations.

    A request to make a PDF/PPT/PPTX/presentation from an existing document is
    a transformation unless the user explicitly asks for a summary/condensation.
    This must not depend on semantic top-k tool selection because generic
    write_pptx can otherwise win and cause the model to author a short summary.
    """
    q = str(query or "").strip().lower()
    if not q:
        return False
    explicit_summary = re.search(
        r"\b(summar(?:y|ize|ise|ized|ised)|condens(?:e|ed|ing)|shorten|executive summary|high[- ]level|abridg(?:e|ed)|\b\d+\s+slides?\s+summary)\b",
        q,
    )
    target = re.search(
        r"\b(pdf|pptx?|powerpoint|presentation|slides?|docx|word|html|markdown)\b",
        q,
    )
    transform_verb = re.search(
        r"\b(format|restyle|typeset|clean up|clean-up|beautify|professionally format|convert|export|render|turn|make|create|transform|produce|generate|build)\b",
        q,
    )
    source_hint = re.search(
        r"\b(this|the|attached|uploaded|source|document|docx|file|world bible|manuscript|report)\b",
        q,
    )
    if explicit_summary:
        return False
    return bool(target and transform_verb and source_hint)


class ContextSelector:
    def __init__(self, store, tools):
        self.store = store
        self.tools = list(tools or [])
        self.index = SemanticIndex(store)

    def select(self, query, tool_limit=6, cognition_limit=4, instruction_limit=3, module_limit=2):
        """Return a bounded candidate bundle. Falls back safely if local embedding is unavailable."""
        query = str(query or "").strip()
        trace_event("retrieval_selection_started", query=query)
        try:
            instructions = InstructionStore(self.store).applicable() if self.store and self.store.exists() else []
            self.index.sync("tools", _tool_records(self.tools))
            self.index.sync("prompt_modules", _module_records())
            self.index.sync("runtime_intents", _intent_records())
            self.index.sync("instructions", _instruction_records(instructions))
            # Cognition indexing is owned by RetrievalStore's hybrid path. Do not
            # eagerly embed/sync the entire cognition graph for every chat turn.
            query_vector = self.index.embed_query(query)
            tool_hits = self.index.search("tools", limit=tool_limit, min_score=0.20, query_vector=query_vector)
            selected_tools = [x["id"] for x in tool_hits]

            # Semantic similarity chooses capability entry points. Expand those entry
            # points through their stored semantic neighbourhood so a mutating tool is
            # not exposed without the closely related inspect/list/read capabilities
            # needed to execute it. This uses stored vectors only: the user query is
            # still embedded exactly once.
            expanded_hits = list(tool_hits)
            seen_tools = set(selected_tools)
            for hit in tool_hits[:3]:
                vector = self.index.item_vector("tools", hit["id"])
                if vector is None:
                    continue
                for neighbor in self.index.search("tools", limit=6, min_score=0.52, query_vector=vector):
                    if neighbor["id"] in seen_tools:
                        continue
                    seen_tools.add(neighbor["id"])
                    selected_tools.append(neighbor["id"])
                    expanded_hits.append(neighbor)
                    if len(selected_tools) >= 14:
                        break
                if len(selected_tools) >= 14:
                    break
            tool_hits = expanded_hits

            document_transform_intent = _looks_like_document_transform(query)
            if document_transform_intent:
                # Do not let semantic similarity choose a generic PPT/PDF writer.
                # Those tools invite the LLM to author a summary instead of
                # transforming the complete source document.
                selected_tools = [n for n in selected_tools if n not in _GENERIC_ARTIFACT_WRITERS]
                for required in ("transform_document", "read_docx"):
                    if any(t.name == required for t in self.tools) and required not in selected_tools:
                        selected_tools.append(required)
                tool_hits = [h for h in tool_hits if h["id"] not in _GENERIC_ARTIFACT_WRITERS]

            intent_hits = self.index.search("runtime_intents", limit=3, min_score=0.0, query_vector=query_vector)
            intent = intent_hits[0]["id"] if intent_hits else "conversation"
            intent_score = float(intent_hits[0]["score"]) if intent_hits else 0.0

            # Project mutation is an execution contract: selecting it causes the
            # loop to force source inspection and require a successful workspace /
            # review mutation before a normal answer may finish. Semantic nearest-
            # neighbour routing is deliberately *not* sufficient to activate that
            # contract; ordinary domain questions can otherwise be misclassified
            # (for example, "how do beasts evolve"). The deterministic predicate
            # requires both a mutation verb and a project/code/UI target. Semantic
            # intent remains useful for non-mutating routing and prompt selection.
            if document_transform_intent:
                intent = "document_transform"
                intent_score = 1.0
            elif _looks_like_cognition_mutation(query):
                intent = "cognition_mutation"
                intent_score = 1.0
            elif _looks_like_project_mutation(query):
                # Mutation wins over inspection when both kinds of words appear.
                intent = "project_mutation"
                intent_score = 1.0
            elif _looks_like_project_inspection(query):
                intent = "project_inspection"
                intent_score = 1.0
            elif intent in {"project_mutation", "cognition_mutation"}:
                # Mutation contracts are never activated by nearest-neighbour similarity alone.
                intent = "conversation"
                intent_score = 0.0

            module_hits = self.index.search("prompt_modules", limit=module_limit, min_score=0.30, query_vector=query_vector)
            modules = [PROMPT_MODULES[x["id"]]["text"] for x in module_hits if x["id"] in PROMPT_MODULES]

            instruction_hits = self.index.search("instructions", limit=instruction_limit, min_score=0.30, query_vector=query_vector)
            selected_instructions = [x["metadata"] for x in instruction_hits]
            # System-scoped instructions are true invariants and remain unconditional.
            selected_instructions.extend(x for x in instructions if x.get("scope") == "system")

            # Cognition retrieval is hybrid: cheap lexical/entity evidence first,
            # semantic similarity only as a supplement when the query contains enough
            # information to search meaningfully. This prevents arbitrary nearest
            # neighbours from being injected for low-information turns.
            retrieval = RetrievalStore(self.store) if self.store and self.store.exists() else None
            candidates = []
            prefetched = None
            if retrieval:
                hybrid = retrieval.search_metadata_candidates(query, limit=cognition_limit)
                trace_event(
                    "retrieval_candidates",
                    query=query,
                    retrieval=hybrid.get("retrieval"),
                    semantic_attempted=hybrid.get("semantic_attempted"),
                    lexical_candidates=hybrid.get("lexical_count"),
                    candidate_count=len(hybrid.get("candidates") or []),
                    candidates=[
                        {
                            "kind": item.get("kind"),
                            "id": item.get("id"),
                            "match": item.get("match"),
                            "lexical_score": item.get("lexical_score"),
                            "semantic_score": item.get("semantic_score"),
                        }
                        for item in (hybrid.get("candidates") or [])
                    ],
                )
                for item in hybrid.get("candidates", []):
                    candidates.append({
                        "kind": item.get("kind"),
                        "id": item.get("id"),
                        "score": item.get("semantic_score"),
                        "lexical_score": item.get("lexical_score"),
                        "match": item.get("match"),
                    })
                if candidates:
                    candidates = retrieval.expand_candidates(candidates, max_depth=2, max_items=24)
                    budget = min(12000, 4000 + 500 * max(0, len(candidates) - cognition_limit))
                    prefetched = retrieval.retrieve_candidates(candidates, detail="compact", max_chars=budget)
                    prefetched["query"] = query
                    prefetched["candidates"] = candidates
                    prefetched["retrieval"] = "hybrid"
                    trace_event(
                        "retrieval_hydrated",
                        query=query,
                        request_count=len(prefetched.get("requests") or []),
                        chars=prefetched.get("used_chars"),
                        truncated=prefetched.get("truncated"),
                        next_requests=len(prefetched.get("next_requests") or []),
                    )
                else:
                    trace_event(
                        "retrieval_hydration_skipped",
                        query=query,
                        reason="no_candidates",
                    )

            return {
                "tool_names": selected_tools,
                "tool_hits": tool_hits,
                "modules": modules,
                "instructions": selected_instructions,
                "prefetched": prefetched,
                "intent": intent,
                "intent_score": intent_score,
                "mode": "semantic",
            }
        except Exception as exc:
            LOGGER.warning("Local semantic selection unavailable; using bounded compatibility fallback: %s", exc)
            trace_event(
                "retrieval_selection_error",
                query=query,
                error=str(exc),
                error_type=exc.__class__.__name__,
                cognition_retrieval="skipped",
            )
            prefetched = None
            return {
                "tool_names": [t.name for t in self.tools],
                "tool_hits": [],
                "modules": [],
                "instructions": InstructionStore(self.store).applicable() if self.store and self.store.exists() else [],
                "prefetched": prefetched,
                "intent": "conversation",
                "intent_score": 0.0,
                "mode": "compatibility_fallback",
            }