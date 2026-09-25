"""One-time source-to-cognition compiler.

Processes source material in chunks so large files (long PDFs, manuscripts,
etc.) are not silently truncated. Each LLM extraction is persisted directly
into the cognition store with provenance linking the extracted knowledge back
to the source artifact(s) that support it.

The compiler supports selective compilation through ``selected_files``.
This is important for reingestion: callers can compile only files affected by
an approved change instead of recompiling the entire project.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from google.genai import types

# Matches a trailing " [part 2/3]" style suffix. This suffix is appended to
# the human-readable "--- SOURCE ARTIFACT: foo.docx [part 2/3] ---" tag in
# _build_batches purely for display/debugging when a large file is split
# across multiple LLM calls. It is NOT part of the actual artifact path, but
# the model is instructed (rule 10) to copy the tag's path "exactly" -- and
# frequently copies the part suffix along with it. Strip it before matching
# against artifact_ids, or every entity from a chunked (multi-part) file
# silently loses its provenance and gets dropped.
_PART_SUFFIX_RE = re.compile(r"\s*\[part\s+\d+\s*/\s*\d+\]\s*$", re.IGNORECASE)

from ragapp.cognition.store import CognitionStore, now_iso
from ragapp.settings import (
    COMPILER_MODEL,
    CHAT_MODEL,
    DEFAULT_CHAT_MODEL,
    MAX_CONTEXT_CHARS,
)
from ragapp.tools.extractors import extract_text
from ragapp.core.metadata_sync import sync_store
from ragapp.config import resolve_model
from ragapp.cognition.code_compiler import CodeCognitionCompiler
from ragapp.cognition.code_parser import detect_language

SCHEMA_PROMPT = """You are the semantic compiler for a persistent cognition system.

Read the supplied source material and compile durable structured knowledge.
Return ONLY valid JSON. Do not write a prose summary as the primary representation.

The cognition model has three separate layers:
1. Entities: durable things and their state.
2. Relationships: durable links between entities.
3. Events: first-class narrative/domain occurrences that anchor change over time.

JSON shape:
{
  "project_type": "fiction|software|research|business|other",
  "timeline": {
    "label": "source-native chapter/section/version/date label or null",
    "sequence": 1,
    "story_time": "source-native story time or null"
  },
  "instructions": "durable domain rules/axioms, concise",
  "entities": {
    "stable_id": {
      "type": "character|artifact|file|module|function|class|location|concept|rule|ability|event|other",
      "name": "human-readable name",
      "tags": [],
      "summary": "compact retrieval summary",
      "attributes": {
        "field": {
          "value": "value",
          "summary": "optional interpretation",
          "timeline": "source-native temporal label or null",
          "valid_from": "source-native temporal value or null",
          "valid_to": "source-native temporal value or null",
          "valid_from_event": "event id or null",
          "valid_to_event": "event id or null",
          "locator": "page/section/line or null"
        }
      },
      "sections": {},
      "events": [{"id": "event_id"}],
      "source_files": ["exact/relative/path.ext"]
    }
  },
  "ledger": {},
  "relationships": {},
  "events": [
    {
      "id": "stable_event_id",
      "type": "event|decision|change|discovery|conflict|resolution|other",
      "title": "short title",
      "description": "what happened",
      "entities": ["stable_id"],
      "location": "location or null",
      "narrative_position": {
        "label": "Chapter 20 / section / version",
        "sequence": 20,
        "story_time": "source-native story time or null"
      },
      "sequence": 20,
      "previous_events": ["event_id"],
      "next_events": ["event_id"],
      "provenance": "source_derived",
      "source_files": ["exact/relative/path.ext"],
      "locator": "page/section/line or null"
    }
  ]
}

Rules:
1. Events are FIRST-CLASS cognition objects. Create an event whenever the source describes a meaningful occurrence, state transition, decision, discovery, conflict, resolution, creation, destruction, arrival, departure, or other change that matters to later reasoning.
2. Do NOT reconstruct events from entity attributes. The compiler must emit events directly in the events array.
3. Every event must have a stable id. Reuse an existing event id from KNOWN EVENTS when the same event is encountered again.
4. Entity state changes should reference the event that caused the change with valid_from_event/valid_to_event when the event is known. A state that begins at an event and remains valid should have valid_to_event=null.
5. Use narrative_position/sequence for ordering. Use absolute dates only when the source provides them. Do not invent chronology.
6. Keep source provenance exact. source_files must contain only files that directly support the record.
7. The supplied material contains headers such as --- SOURCE ARTIFACT: calculator.py ---. Copy the exact relative path into source_files.
8. If an entity is supported by calculator.py only, use ["calculator.py"]. If supported by multiple files, list those files. Never invent a path.
9. If KNOWN ENTITIES or KNOWN EVENTS are supplied, reuse their ids instead of creating duplicates.
10. Create separate entities for reusable concepts/artifacts/modules/classes/functions/locations/etc.; use relationships instead of duplicating shared information.
11. Preserve all useful sections and interpreted content. Keep them concise but do not discard material merely because it is not an attribute.
12. Do not omit source_files. If provenance cannot be determined, omit the record rather than fabricating provenance.
13. Return empty arrays/objects where appropriate.

Return ONLY JSON. No markdown. No explanation.
"""

def _human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def list_available_files(store: CognitionStore):
    """List every file currently sitting in Source and Workspace.

    Each entry identifies a file by ``(area, relative_path)``. That pair is
    exactly what ``compile_project(selected_files=...)`` expects.
    """
    out = []

    for area_name, root in (
        ("source", store.source),
        ("workspace", store.workspace),
    ):
        if not root.exists():
            continue

        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            # Internal application state is never a compilable project artifact.
            rel = p.relative_to(root).as_posix()
            if area_name == "workspace" and (rel == "drafts" or rel.startswith("drafts/") or rel == ".system" or rel.startswith(".system/")):
                continue

            out.append(
                {
                    "area": area_name,
                    "path": p.relative_to(root).as_posix(),
                    "size": _human_size(p.stat().st_size),
                }
            )

    return out


def _split_into_pieces(text, max_chars):
    """Split text on paragraph boundaries into pieces <= max_chars.

    A single paragraph longer than max_chars is hard-sliced so nothing is
    silently dropped.
    """
    paras = [p for p in text.split("\n\n") if p.strip()]
    pieces = []
    cur = ""

    for p in paras:
        if len(p) > max_chars:
            if cur:
                pieces.append(cur)
                cur = ""

            for i in range(0, len(p), max_chars):
                pieces.append(p[i : i + max_chars])

            continue

        if cur and len(cur) + len(p) + 2 > max_chars:
            pieces.append(cur)
            cur = p
        else:
            cur = (cur + "\n\n" + p).strip() if cur else p

    if cur:
        pieces.append(cur)

    return pieces or [""]


def _build_batches(files, per_call_budget):
    """Turn files into labeled pieces and greedily pack them into batches.

    Each batch item is:

        (area, relative_path, labeled_text)

    Keeping area and relative_path attached to every piece is important
    because the compiler uses them later to resolve source provenance.
    """
    labeled = []

    for area_name, root, p in files:
        try:
            text = extract_text(p)
        except Exception as exc:
            text = f"[Extraction failed for {p.name}: {exc}]"

        rel = p.relative_to(root).as_posix()

        pieces = _split_into_pieces(
            text,
            per_call_budget,
        )

        total = len(pieces)

        for idx, piece in enumerate(pieces, 1):
            tag = (
                f"--- {area_name.upper()} ARTIFACT: {rel}"
                + (
                    f" [part {idx}/{total}]"
                    if total > 1
                    else ""
                )
                + " ---"
            )

            labeled.append(
                (
                    area_name,
                    rel,
                    f"\n{tag}\n{piece}",
                )
            )

    batches = []
    cur = []
    cur_len = 0

    for area_name, rel, piece in labeled:
        if cur and cur_len + len(piece) > per_call_budget:
            batches.append(cur)
            cur = []
            cur_len = 0

        cur.append(
            (
                area_name,
                rel,
                piece,
            )
        )

        cur_len += len(piece)

    if cur:
        batches.append(cur)

    return batches


def _merge_instructions(acc_text, new_text):
    """Merge instruction lines without duplicating identical lines."""
    if not new_text:
        return acc_text

    existing_lines = {
        line.strip()
        for line in acc_text.splitlines()
        if line.strip()
    }

    new_lines = [
        line
        for line in new_text.splitlines()
        if line.strip()
        and line.strip() not in existing_lines
    ]

    if acc_text:
        return (
            acc_text
            + "\n"
            + "\n".join(new_lines)
        ).strip()

    return new_text


def _normalise_source_file(path):
    """Normalise an LLM-returned source path.

    The compiler prompt requires paths exactly as supplied in artifact
    headers. This helper additionally tolerates a leading './' and
    ``source/`` or ``workspace/`` prefix.
    """
    if not isinstance(path, str):
        return None

    path = path.strip().replace("\\", "/")

    # Drop a model-echoed "[part N/M]" chunk-label suffix (see
    # _PART_SUFFIX_RE above) before it's mistaken for part of the path.
    path = _PART_SUFFIX_RE.sub("", path).strip()

    if not path:
        return None

    while path.startswith("./"):
        path = path[2:]

    if path.startswith("source/"):
        return ("source", path[len("source/") :])

    if path.startswith("workspace/"):
        return ("workspace", path[len("workspace/") :])

    # No area prefix. This is actually the NORMAL case: the artifact tag
    # headers built in _build_batches (e.g. "--- SOURCE ARTIFACT: foo.docx
    # ---") and the SCHEMA_PROMPT instructions both tell the model to return
    # the bare relative path with no "source/"/"workspace/" prefix. Leave the
    # area unresolved here; the caller tries each known area.
    return (None, path)


def _resolve_source_artifacts(
    source_files,
    artifact_ids,
    batch_artifacts,
):
    """Resolve LLM-declared source paths to artifact IDs.

    Provenance must be explicit. We never automatically associate an entity,
    relationship, or event with every file in an LLM batch merely because
    those files happened to share the same API call.

    When a batch contains exactly one artifact, that artifact is the only
    possible provenance for facts extracted from that batch. In that case,
    missing source_files is normalised to the sole artifact. For multi-file
    batches, provenance remains explicit and ambiguous records are rejected.
    """
    resolved = []

    if not isinstance(source_files, list):
        source_files = []

    # A single-artifact batch has unambiguous provenance. This is especially
    # important for ordinary documents such as Markdown notes: the model may
    # omit source_files even though the compiler knows exactly which artifact
    # supplied the material. Do not make this fallback for multi-file batches.
    if not source_files and len(batch_artifacts) == 1:
        return list(batch_artifacts)

    for source_file in source_files:
        normalised = _normalise_source_file(source_file)

        if normalised is None:
            continue

        area, rel = normalised

        if area is not None:
            artifact_id = artifact_ids.get((area, rel))

            if artifact_id:
                resolved.append(artifact_id)

            continue

        # The model returned a bare path with no area prefix (the expected
        # case per the prompt/tag format). Try each known area, preferring
        # "source" since that's what compile_project treats as authoritative.
        for candidate_area in ("source", "workspace"):
            artifact_id = artifact_ids.get((candidate_area, rel))

            if artifact_id:
                resolved.append(artifact_id)
                break

    return sorted(set(resolved))

def _build_artifact_ids(store, files):
    """Register every file participating in this compilation."""
    artifact_ids = {}

    for area_name, root, p in files:
        rel = p.relative_to(root).as_posix()

        artifact = store.register_artifact(
            area=area_name,
            path=rel,
        )
        artifact_ids[(area_name, rel)] = artifact["id"] if isinstance(artifact, dict) else artifact

    return artifact_ids


def compile_project(
    store: CognitionStore,
    progress_callback=None,
    selected_files=None,
    reconcile_selected=True,
):
    """Compile project files additively, preserving existing cognition.

    Compilation is monotonic by default: a later compile may add new source
    observations and provenance, but it never deletes an existing cognition
    projection merely because a source file was selected again. Durable
    chat/agent state remains authoritative over source observations.

    ``reconcile_selected`` is retained for API compatibility only. Destructive
    source-projection replacement is intentionally not part of compilation.
    """
    if selected_files is not None:
        selected_files = list(selected_files)

    # Compilation can mutate canonical cognition, so preserve the exact
    # pre-compilation project state in Git before any new observations are
    # merged. Git is the recovery layer; the filesystem-backed cognition
    # remains authoritative at runtime.
    pre_state_git_commit = store.commit_authoritative_change(
        "Pre-state backup before cognition compilation"
    )

    needs_rollback = (
        reconcile_selected
        and selected_files is not None
        and any(area == "source" for area, _ in selected_files)
    )

    if not needs_rollback:
        result = _compile_project_impl(
            store,
            progress_callback=progress_callback,
            selected_files=selected_files,
            reconcile_selected=reconcile_selected,
        )
        if isinstance(result, dict):
            result.setdefault("pre_state_git_commit", pre_state_git_commit)
        return result

    with tempfile.TemporaryDirectory(prefix="cognition-compile-") as tmp:
        backup = Path(tmp) / "cognition"
        # The SQLite metadata connection may be open on Windows, so snapshot
        # only the authoritative file-backed cognition state. The SQLite index
        # is derived from that state and is synchronized after rollback.
        shutil.copytree(store.cognition, backup)

        try:
            result = _compile_project_impl(
                store,
                progress_callback=progress_callback,
                selected_files=selected_files,
                reconcile_selected=reconcile_selected,
            )
            if isinstance(result, dict):
                result.setdefault("pre_state_git_commit", pre_state_git_commit)
            return result
        except Exception:
            if store.cognition.exists():
                shutil.rmtree(store.cognition)
            shutil.copytree(backup, store.cognition)
            sync_store(store)
            raise


def _compile_project_impl(
    store: CognitionStore,
    progress_callback=None,
    selected_files=None,
    reconcile_selected=True,
):
    """Compile selected project files into the cognition store.

    ``selected_files`` is an optional iterable of:

        (area, relative_path)

    Example:

        [
            ("source", "calculator.py"),
            ("source", "billing.py"),
        ]

    For authoritative cognition, callers should pass SOURCE files only.

    Workspace files may still be explicitly selected by the compilation UI
    when needed for inspection, but workspace content must not be treated as
    authoritative project state.

    If selected_files is None, the existing compatibility behavior of
    compiling every available source/workspace file is preserved.

    IMPORTANT:

    This function performs only the compilation requested by selected_files.
    It does NOT automatically compile the entire project when only a subset
    is selected. This is what allows reingestion to remain selective.
    """

    selected_set = (
        set(selected_files)
        if selected_files is not None
        else None
    )

    files = []

    for area_name, root in (
        ("source", store.source),
        ("workspace", store.workspace),
    ):
        if not root.exists():
            continue

        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue

            rel = p.relative_to(root).as_posix()

            # Internal application state is never a compilable project artifact.
            if area_name == "workspace" and (
                rel == "drafts"
                or rel.startswith("drafts/")
                or rel == ".system"
                or rel.startswith(".system/")
            ):
                continue

            if (
                selected_set is not None
                and (area_name, rel) not in selected_set
            ):
                continue

            files.append(
                (
                    area_name,
                    root,
                    p,
                )
            )

    # Source code is compiled deterministically. It must never be sent to the
    # LLM compiler. Non-code source material continues through the LLM path.
    code_files = [
        (area, root, p)
        for area, root, p in files
        if area == "source" and detect_language(p)
    ]
    llm_files = [
        item for item in files
        if item not in code_files
    ]

    # IMPORTANT: recompilation is additive. Do not remove the previous source
    # projection here. Existing cognition is persistent state; recompiling a
    # source only contributes observations that are not already present.
    # Explicit user/agent state changes are durable and are resolved ahead of
    # source-derived observations by CognitionStore.latest_attribute_state().

    deterministic_result = None
    if code_files:
        deterministic_result = CodeCognitionCompiler(store).compile_files(
            [p for _, _, p in code_files]
        )

    # If the selection contains only code, no API key/model is required.
    if not llm_files:
        store.ensure_initialized()
        store.mark_compiled()
        sync_store(store)
        commit_id = store.commit_authoritative_change("Compile canonical cognition")
        return {
            "status": "compiled",
            "project_id": store.project_id,
            "source_files": len(code_files),
            "workspace_files": 0,
            "entities": len(store.master_metadata().get("entities", {})),
            "relationships": len(store.relationships()),
            "chunks": 0,
            "deterministic_code": deterministic_result or {},
            "skipped_chunks": [],
            "git_commit": commit_id,
        }

    # All non-code documents now use the structure-aware compiler.
    # It parses PDF/DOCX/PPTX/XLSX/HTML/text into a canonical structural
    # representation, persists metadata-only locators, and sends only the
    # current structure-aware segment text to the LLM.
    from ragapp.cognition.document_compiler import DocumentCognitionCompiler

    document_result = DocumentCognitionCompiler(store).compile_files(
        [(area, p) for area, _, p in llm_files],
        progress_callback=progress_callback,
    )

    document_result["deterministic_code"] = deterministic_result or {}
    document_result["source_files"] = sum(
        1 for area, _, _ in files if area == "source"
    )
    document_result["workspace_files"] = sum(
        1 for area, _, _ in files if area == "workspace"
    )
    sync_store(store)
    document_result["git_commit"] = store.commit_authoritative_change("Compile canonical cognition")
    return document_result
