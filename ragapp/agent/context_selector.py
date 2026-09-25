"""Semantic, local pre-call context selection.

One local query embedding selects candidate tools, cognition and situational
instructions.  The main LLM remains the only generative semantic reasoner.
"""
from __future__ import annotations

import json
import logging

from ragapp.core.semantic_index import SemanticIndex
from ragapp.core.retrieval import RetrievalStore
from ragapp.core.instructions import InstructionStore

LOGGER = logging.getLogger("ragapp.agent.context_selector")

# These are retrieval records, not globally injected prompts.
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

_ALWAYS_TOOL_NAMES = {"search_cognition_metadata", "request_cognition_context"}


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
    records = []
    for kind in ("entity", "relationship", "event", "location", "concept", "definition", "knowledge"):
        for rec in store._all_kind_records(kind):
            ident = str(rec.get("id") or "").strip()
            if not ident:
                continue
            searchable = {
                k: rec.get(k)
                for k in (
                    "id", "name", "title", "type", "description", "summary", "state",
                    "participants", "relation_type", "effects", "location",
                    "valid_from", "valid_to",
                )
                if rec.get(k) not in (None, "", [], {})
            }
            records.append({
                "id": f"{kind}:{ident}",
                "text": f"{kind}. " + json.dumps(searchable, ensure_ascii=False, default=str),
                "metadata": {"kind": kind, "object_id": ident},
            })
    return records


def _instruction_records(instructions):
    out = []
    for item in instructions:
        if item.get("scope") == "system":
            continue
        content = str(item.get("content") or "").strip()
        if content:
            out.append({"id": str(item.get("id")), "text": content, "metadata": item})
    return out


def _module_records():
    return [
        {"id": key, "text": value["description"], "metadata": {"key": key}}
        for key, value in PROMPT_MODULES.items()
    ]


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
            self.index.sync("instructions", _instruction_records(instructions))
            if self.store and self.store.exists():
                self.index.sync("cognition", _cognition_records(self.store))

            query_vector = self.index.embed_query(query)
            tool_hits = self.index.search("tools", limit=tool_limit, min_score=0.20, query_vector=query_vector)
            selected_tools = [x["id"] for x in tool_hits]
            # Do not force large cognition schemas into every model request.
            # They remain discoverable through the local capability escape hatch.

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
            prefetched = RetrievalStore(self.store).retrieve_candidates(candidates, detail="compact", max_chars=4000) if candidates else None
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
                "mode": "compatibility_fallback",
            }
