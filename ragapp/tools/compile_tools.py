"""Agent tool that recompiles authoritative source documents into cognition.

Compilation was previously reachable only from the Streamlit "Compile" button and
after draft approval, so an agent asked to "recompile the source" had no tool to
call. This wraps the same pipeline the UI uses.

Compilation is additive (existing cognition is never deleted) and
``compile_project`` commits a pre-state backup to Git before it merges anything,
so a bad compile can be rolled back from history.
"""
from ragapp.tools.definitions import Tool


def _source_pairs(store, files):
    """Return [("source", relpath), ...] for the requested files, or every source file."""
    from ragapp.core.reingest import ReingestPipeline

    all_pairs = ReingestPipeline(store)._source_files()
    if not files:
        return all_pairs

    wanted = []
    for f in files:
        value = str(f or "").replace("\\", "/").strip()
        while value.startswith("./"):
            value = value[2:]
        if value.startswith("source/"):
            value = value[len("source/"):]
        wanted.append(value)

    known = {p[1] for p in all_pairs}
    missing = [w for w in wanted if w not in known]
    if missing:
        raise FileNotFoundError(
            "Not found in /source: " + ", ".join(missing)
            + ". Use list_project_files(area='source') to see valid paths."
        )
    return [("source", w) for w in wanted]


def recompile_source(store, files=None):
    # Imported lazily: ragapp.cognition.compiler imports ragapp.tools.extractors,
    # so a top-level import here would be circular during package import.
    from ragapp.cognition.compiler import compile_project

    pairs = _source_pairs(store, files)
    if not pairs:
        return {"status": "error", "error": "There are no source files to compile."}

    result = compile_project(store, selected_files=pairs, reconcile_selected=True)
    summary = {"status": "compiled", "files_requested": [p[1] for p in pairs]}
    if isinstance(result, dict):
        for key in ("status", "source_files", "workspace_files", "chunks", "entities",
                    "relationships", "segments", "processed_segments", "skipped_segments",
                    "error", "errors"):
            if key in result:
                summary["compiler_status" if key == "status" else key] = result[key]
    return summary


def build_compile_tools(store):
    return [
        Tool(
            "recompile_source",
            "Recompile / rebuild / re-ingest the authoritative source documents into project cognition "
            "(entities, relationships, events, concepts). Use ONLY when the user explicitly asks to compile, "
            "recompile, ingest, or rebuild cognition from source. Compiles every source file unless 'files' is "
            "given. Additive: existing cognition is preserved and a Git backup is taken first. May take several "
            "minutes and calls the LLM once per document segment.",
            {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional source-relative paths (no 'source/' prefix). Omit to compile all source files.",
                    },
                },
            },
            lambda files=None: recompile_source(store, files),
        ),
    ]