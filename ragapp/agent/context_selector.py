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
    "conversation": {
        "description": (
            "The user is asking a general question, discussing an idea, or requesting prose that "
            "does not require changing project files."
        ),
    },
}

PROMPT_MODULES = {
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
        "description": "Read or manipulate PDF DOCX PPTX XLSX XML and other structured document formats.",
        "text": "Use the format-specific tool selected for the task and preserve document structure unless the user requests conversion.",
    },
    "mermaid": {
        "description": "Create Mermaid diagrams, flowcharts, relationship diagrams, architecture diagrams or visual graphs.",
        "text": "For Mermaid, return one syntactically complete Mermaid fenced block. Prefer simple flowchart syntax and balanced subgraphs.",
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


class ContextSelector:
    def __init__(self, store, tools):
        self.store = store
        self.tools = list(tools or [])
        self.index = SemanticIndex(store)

    def select(self, query, tool_limit=6, cognition_limit=4, instruction_limit=3, module_limit=2):
        """Return a bounded candidate bundle. Falls back safely if local embedding is unavailable."""
        query = str(query or "").strip()
        try:
            instructions = InstructionStore(self.store).applicable() if self.store and self.store.exists() else []
            self.index.sync("tools", _tool_records(self.tools))
            self.index.sync("prompt_modules", _module_records())
            self.index.sync("runtime_intents", _intent_records())
            self.index.sync("instructions", _instruction_records(instructions))
            if self.store and self.store.exists():
                self.index.sync("cognition", _cognition_records(self.store))

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
            if _looks_like_project_mutation(query):
                intent = "project_mutation"
                intent_score = 1.0
            elif intent == "project_mutation":
                intent = "conversation"
                intent_score = 0.0

            module_hits = self.index.search("prompt_modules", limit=module_limit, min_score=0.30, query_vector=query_vector)
            modules = [PROMPT_MODULES[x["id"]]["text"] for x in module_hits if x["id"] in PROMPT_MODULES]

            instruction_hits = self.index.search("instructions", limit=instruction_limit, min_score=0.30, query_vector=query_vector)
            selected_instructions = [x["metadata"] for x in instruction_hits]
            # System-scoped instructions are true invariants and remain unconditional.
            selected_instructions.extend(x for x in instructions if x.get("scope") == "system")

            cognition_hits = self.index.search("cognition", limit=cognition_limit, min_score=0.25, query_vector=query_vector) if self.store and self.store.exists() else []
            candidates = []
            seen = set()
            for x in cognition_hits:
                kind = x["metadata"].get("kind")
                ident = x["metadata"].get("object_id")
                key = (str(kind or ""), str(ident or ""))
                if not all(key) or key in seen:
                    continue
                seen.add(key)
                candidates.append({"kind": kind, "id": ident, "score": x["score"]})
            # Embeddings locate likely cognition objects; canonical graph edges then
            # expand those entry points. Semantic top-k is never treated as the full
            # answer set.
            retrieval = RetrievalStore(self.store) if self.store and self.store.exists() else None
            if retrieval and candidates:
                candidates = retrieval.expand_candidates(candidates, max_depth=2, max_items=24)
            budget = min(12000, 4000 + 500 * max(0, len(candidates) - cognition_limit))
            prefetched = retrieval.retrieve_candidates(candidates, detail="compact", max_chars=budget) if retrieval and candidates else None
            if prefetched is not None:
                prefetched["query"] = query
                prefetched["candidates"] = candidates
                prefetched["retrieval"] = "local_semantic"

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
            prefetched = None
            if self.store and self.store.exists():
                try:
                    prefetched = RetrievalStore(self.store).retrieve_for_query(query, max_chars=8000, limit=cognition_limit, detail="compact")
                except Exception:
                    LOGGER.exception("Fallback cognition retrieval failed")
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