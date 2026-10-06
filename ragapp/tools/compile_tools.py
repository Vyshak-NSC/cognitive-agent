"""Agent tool that recompiles authoritative source documents into cognition.

Compilation was previously reachable only from the Streamlit "Compile" button and
after draft approval, so an agent asked to "recompile the source" had no tool to
call. This wraps the same pipeline the UI uses.

Compilation is additive (existing cognition is never deleted) and
``compile_project`` commits a pre-state backup to Git before it merges anything,
so a bad compile can be rolled back from history.
"""
import hashlib
from ragapp.tools.definitions import Tool


def _canonical_record_digests(store):
    """Return a content signature for persisted canonical cognition records."""
    digests = {}
    for kind in ("entities", "relationships", "events", "locations", "concepts", "definitions", "knowledge"):
        root = store.cognition / kind
        if not root.exists():
            continue
        for path in root.rglob("*.json"):
            relative = path.relative_to(store.cognition).as_posix()
            digests[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


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


def recompile_source(store, files=None, semantic=True):
    # Imported lazily: ragapp.cognition.compiler imports ragapp.tools.extractors,
    # so a top-level import here would be circular during package import.
    from ragapp.cognition.compiler import compile_project

    pairs = _source_pairs(store, files)
    if not pairs:
        return {"status": "error", "error": "There are no source files to compile."}

    before = _canonical_record_digests(store)
    result = compile_project(
        store,
        selected_files=pairs,
        reconcile_selected=True,
        semantic_enrichment=bool(semantic),
    )
    after = _canonical_record_digests(store)
    if not isinstance(result, dict):
        return {
            "status": "error",
            "error": "The cognition compiler returned no result; compilation was not verified.",
            "files_requested": [p[1] for p in pairs],
        }

    compiler_status = str(result.get("status") or "").lower()
    failed_files = result.get("failed_files") or []
    compiler_errors = result.get("errors") or []
    succeeded = (
        compiler_status in {"compiled", "ok", "success"}
        and not failed_files
        and not compiler_errors
    )
    summary = {
        "status": "compiled" if succeeded else "error",
        "files_requested": [p[1] for p in pairs],
        "compiler_status": compiler_status or "unknown",
        "semantic_enrichment_requested": bool(semantic),
        "canonical_records_created": len(after.keys() - before.keys()),
        "canonical_records_updated": sum(
            before[path] != after[path]
            for path in before.keys() & after.keys()
        ),
    }
    if not succeeded:
        if failed_files:
            summary["error"] = f"Compilation failed for source files: {failed_files}"
        elif compiler_errors:
            summary["error"] = f"The cognition compiler reported errors: {compiler_errors}"
        else:
            summary["error"] = str(
                result.get("error")
                or "The cognition compiler did not report a successful compilation."
            )
    for key in (
        "source_files", "workspace_files", "chunks", "documents", "entities",
        "relationships", "events", "locations", "concepts", "definitions",
        "knowledge", "segments", "processed_segments", "skipped_segments",
        "semantic_enrichment", "failed_files", "errors",
    ):
        if key in result:
            summary[key] = result[key]
    summary["canonical_records_created"] = len(after.keys() - before.keys())
    summary["canonical_records_updated"] = sum(
        before[path] != after[path]
        for path in before.keys() & after.keys()
    )
    return summary


def build_compile_tools(store):
    return [
        Tool(
            "recompile_source",
            "Recompile / rebuild / re-ingest the authoritative source documents into project cognition "
            "(entities, relationships, events, concepts). Use ONLY when the user explicitly asks to compile, "
            "recompile, ingest, or rebuild cognition from source. Compiles every source file unless 'files' is "
            "given. Additive: existing cognition is preserved and a Git backup is taken first. May take several "
            "minutes. Semantic enrichment is optional; set semantic=false for a zero-LLM structural/canonical ingestion.",
            {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional source-relative paths (no 'source/' prefix). Omit to compile all source files.",
                    },
                    "semantic": {
                        "type": "boolean",
                        "default": True,
                        "description": "Request semantic LLM enrichment. Set false when only deterministic code/structure ingestion is needed.",
                    },
                },
            },
            lambda files=None, semantic=True: recompile_source(store, files, semantic=semantic),
        ),
    ]