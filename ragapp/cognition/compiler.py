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

SCHEMA_PROMPT = """You are the one-time compiler for a persistent cognition system.

Read the supplied source material and compile durable structured knowledge.
Do not write prose summaries as the primary representation.

Return ONLY valid JSON with this shape:

{
  "project_type": "string",

  "instructions": "domain rules and user axioms, concise",

  "entities": {
    "stable_id": {
      "type": "character|artifact|file|module|concept|location|rule|other",
      "name": "human-readable name",
      "tags": [],
      "summary": "",
      "attributes": {},
      "provenance": "user_stated|source_derived|inferred",
      "source_files": [
        "exact/relative/path.py"
      ]
    }
  },

  "ledger": {
    "stable_id": {
      "field": "current value"
    }
  },

  "relationships": {
    "stable_relationship_id": {
      "type": "relationship|dependency|derivation",
      "tags": [],
      "source": "stable entity id",
      "target": "stable entity id",
      "state": "",
      "provenance": "user_stated|source_derived|inferred",
      "source_files": [
        "exact/relative/path.py"
      ]
    }
  },

  "events": [
    {
      "event": "",
      "entities": [],
      "provenance": "source_derived",
      "source_files": [
        "exact/relative/path.py"
      ]
    }
  ]
}

Rules:

1. Create stable IDs that can be referenced later.

2. Preserve explicit facts.

3. Do not invent facts.

4. If a KNOWN ENTITIES section is provided, reuse an existing stable_id
   whenever the same entity appears again.

5. Every entity MUST contain source_files.

6. Every relationship MUST contain source_files.

7. Every event MUST contain source_files.

8. source_files MUST contain only files that directly support the
   corresponding entity, relationship, or event.

9. Do NOT assign an entity to every file in the current batch merely because
   those files were supplied together.

10. The supplied material contains headers such as:

    --- SOURCE ARTIFACT: calculator.py ---

    or:

    --- SOURCE ARTIFACT: billing.py ---

    Use those exact relative paths in source_files.

11. If an entity is supported by calculator.py only, return:

    "source_files": ["calculator.py"]

12. If an entity is supported by billing.py and calculator.py, return:

    "source_files": ["billing.py", "calculator.py"]

13. Never invent a source_files path.

14. Do not omit source_files. If you cannot determine the supporting file
    from the supplied material, do not fabricate one.

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

    # A selective compile is a replacement projection for the selected source
    # files. Remove the old projection BEFORE either deterministic or LLM
    # compilation so the newly compiled cognition represents the new source.
    if reconcile_selected and selected_set is not None:
        selected_source_artifacts = {
            f"source:{rel}"
            for area, rel in selected_set
            if area == "source"
        }
        if selected_source_artifacts:
            store.remove_source_projection(selected_source_artifacts)

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
        }

    # The LLM compiler receives only non-code files.
    files = llm_files

    if not files:
        store.ensure_initialized()
        sync_store(store)

        status = (
            "no_files_selected"
            if (
                selected_set is not None
                and len(selected_set) == 0
            )
            else "initialized_empty"
        )

        return {
            "status": status,
            "project_id": store.project_id,
            "source_files": 0,
            "workspace_files": 0,
            "entities": 0,
            "relationships": 0,
            "chunks": 0,
        }

    from ragapp.config import (
        get_api_key,
        load_project_config,
    )

    provider_name = (
        load_project_config(store)
        .get("provider", {})
        .get("name", "gemini")
        .lower()
    )

    key = get_api_key(
        store,
        provider_name,
    )

    if not key:
        raise RuntimeError(
            f"No {provider_name.title()} API key configured. "
            "Add it in Settings or set the matching "
            "environment variable."
        )

    model = resolve_model(
        store,
        COMPILER_MODEL or CHAT_MODEL,
        DEFAULT_CHAT_MODEL,
        provider=provider_name,
    )

    if provider_name == "openrouter":
        from openai import OpenAI

        _client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=key,
            timeout=120.0,
            max_retries=0,
        )

        def _generate_json(prompt_text):
            resp = _client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt_text,
                    }
                ],
                response_format={
                    "type": "json_object",
                },
                max_tokens=8000,
            )

            content = resp.choices[0].message.content

            if content is None:
                raise RuntimeError(
                    "OpenRouter returned no text content. "
                    f"finish_reason="
                    f"{getattr(resp.choices[0], 'finish_reason', None)}, "
                    f"message={resp.choices[0].message!r}"
                )

            return content

    elif provider_name == "gemini":
        from google import genai

        llm = genai.Client(
            api_key=key,
        )

        def _generate_json(prompt_text):
            resp = llm.models.generate_content(
                model=model,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(
                                text=prompt_text,
                            )
                        ],
                    )
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )

            return resp.text

    else:
        raise RuntimeError(
            f"'{provider_name}' is configured as the provider "
            "but the cognition compiler only supports "
            "'gemini' and 'openrouter' right now."
        )

    # Leave headroom for the schema prompt and known-entity context.
    per_call_budget = max(
        int(MAX_CONTEXT_CHARS * 0.7),
        4000,
    )

    batches = _build_batches(
        files,
        per_call_budget,
    )

    # Every selected file receives a stable artifact ID. The artifact ID is
    # what persisted cognition uses for provenance.
    artifact_ids = _build_artifact_ids(
        store,
        files,
    )


    # Seed from existing cognition rather than starting blank.
    #
    # This is important because a selective compile must not erase cognition
    # belonging to files that were NOT selected for this run.
    entities = (
        store.state_map().get("entities", {})
        if store.exists()
        else {}
    )

    ledger = (
        store.ledger()
        if store.exists()
        else {}
    )

    relationships = (
        store.relationships()
        if store.exists()
        else {}
    )

    instructions_text = (
        store.instructions_path.read_text(
            encoding="utf-8"
        )
        if store.instructions_path.exists()
        else ""
    )

    events = []
    project_type = "domain"
    skipped_chunks = []

    for i, batch in enumerate(batches, 1):
        timestamp = now_iso()

        # These are ALL artifacts physically represented in this LLM batch.
        #
        # This is only a fallback. Precise entity/relationship/event
        # provenance comes from source_files in the LLM response.
        batch_artifacts = {
            artifact_ids[(area, rel)]
            for area, rel, _ in batch
            if (area, rel) in artifact_ids
        }

        known_summary = {
            sid: e.get("summary", "")
            for sid, e in entities.items()
            if isinstance(e, dict)
        }

        prompt_parts = [
            SCHEMA_PROMPT,
        ]

        if known_summary:
            prompt_parts.append(
                "\nKNOWN ENTITIES SO FAR:\n"
                + json.dumps(
                    known_summary,
                    ensure_ascii=False,
                )
            )

        prompt_parts.append(
            f"\nSOURCE MATERIAL "
            f"(chunk {i} of {len(batches)}):\n"
            + "".join(
                piece
                for _, _, piece in batch
            )
        )

        prompt = "".join(prompt_parts)

        try:
            response_text = _generate_json(prompt)

        except Exception as exc:
            raise RuntimeError(
                f"LLM call failed on batch "
                f"{i}/{len(batches)} "
                f"(provider={provider_name}, "
                f"model={model}): {exc!r}"
            ) from exc

        try:
            data = json.loads(response_text)

        except json.JSONDecodeError:
            # Retry once with an explicit instruction to return valid,
            # complete JSON.
            repair_prompt = (
                prompt
                + "\n\n"
                "Your previous response was not valid JSON. "
                "Return ONLY complete, valid JSON matching the "
                "schema, with no truncation and no trailing commas."
            )

            try:
                data = json.loads(
                    _generate_json(
                        repair_prompt
                    )
                )

            except Exception:
                skipped_chunks.append(i)

                if progress_callback:
                    progress_callback(
                        i,
                        len(batches),
                    )

                continue

        if not isinstance(data, dict):
            skipped_chunks.append(i)

            if progress_callback:
                progress_callback(
                    i,
                    len(batches),
                )

            continue

        # Record the exact source material sent to the model. This is local
        # provenance/index data and does not cost an API call.
        for area, rel, piece in batch:
            artifact_id = artifact_ids.get((area, rel))
            if artifact_id:
                store.record_compilation_chunk(
                    artifact_id,
                    i,
                    piece,
                    locator=f"chunk {i}",
                )

        project_type = (
            data.get("project_type")
            or project_type
        )

        instructions_text = _merge_instructions(
            instructions_text,
            data.get("instructions", ""),
        )

        new_events = data.get(
            "events",
            [],
        )

        new_events = (
            new_events
            if isinstance(new_events, list)
            else []
        )

        events.extend(new_events)

        # --------------------------------------------------------------
        # ENTITIES
        # --------------------------------------------------------------

        chunk_entities = data.get("entities", {}) or {}

        if not isinstance(chunk_entities, dict):
            chunk_entities = {}

        for sid, e in chunk_entities.items():
            if not isinstance(e, dict):
                continue

            source_artifacts = _resolve_source_artifacts(
                e.get("source_files", []),
                artifact_ids,
                batch_artifacts,
            )

            # Never fabricate provenance.
            #
            # If the LLM failed to tell us which file supports this entity, do not
            # attach the entity to every file in the batch. That would corrupt
            # source_projections and make selective reingestion unreliable.
            if not source_artifacts:
                skipped_chunks.append(
                    {
                        "chunk": i,
                        "reason": "entity_missing_source_provenance",
                        "entity_id": str(sid),
                    }
                )
                continue

            for source_artifact in source_artifacts:
                store.upsert_entity_update(
                    {
                        "id": sid,
                        "type": e.get("type", "other"),
                        "name": e.get("name", sid),
                        "summary": e.get("summary", ""),
                        "tags": e.get("tags", []),
                        "attributes": e.get("attributes", {}),
                    },
                    source_artifact=source_artifact,
                    chunk_index=i,
                    default_timeline=f"chunk {i}",
                )

            entities.setdefault(sid, {})["summary"] = e.get(
                "summary",
                "",
            )

        # --------------------------------------------------------------
        # LEDGER
        # --------------------------------------------------------------

        chunk_ledger = data.get("ledger", {}) or {}
        if isinstance(chunk_ledger, dict):
            for sid, fields in chunk_ledger.items():
                if not isinstance(fields, dict):
                    continue

                source_artifacts = _resolve_source_artifacts(
                    [],
                    artifact_ids,
                    batch_artifacts,
                )

                for source_artifact in source_artifacts:
                    store.upsert_entity_update(
                        {
                            "id": sid,
                            "attributes": fields,
                        },
                        source_artifact=source_artifact,
                        chunk_index=i,
                        default_timeline=f"chunk {i}",
                    )

        # --------------------------------------------------------------
        # RELATIONSHIPS
        # --------------------------------------------------------------

        chunk_relationships = (
            data.get("relationships", {})
            or {}
        )

        if not isinstance(
            chunk_relationships,
            dict,
        ):
            chunk_relationships = {}

        for sid, rel in chunk_relationships.items():
            if not isinstance(rel, dict):
                continue

            source_artifacts = _resolve_source_artifacts(
                rel.get("source_files", []),
                artifact_ids,
                batch_artifacts,
            )

            # Preserve the relationship itself while attaching the exact
            # artifact provenance where the store API supports it.
            for source_artifact in source_artifacts:
                store.add_relationship(
                    rel,
                    timeline=f"chunk {i}",
                    source_artifact=source_artifact,
                )

        # --------------------------------------------------------------
        # EVENTS
        # --------------------------------------------------------------

        for event in new_events:
            if not isinstance(event, dict):
                continue

            source_artifacts = _resolve_source_artifacts(
                event.get("source_files", []),
                artifact_ids,
                batch_artifacts,
            )

            if not source_artifacts:
                skipped_chunks.append(i)
                continue

            for source_artifact in source_artifacts:
                store.add_event(
                    event,
                    timeline=f"chunk {i}",
                    source_artifact=source_artifact,
                )

        # Persist project-level compiler state after each successful batch.
        store.set_project_type(project_type)
        store.instructions_path.write_text(
            instructions_text,
            encoding="utf-8",
        )

        sync_store(store)

        if progress_callback:
            progress_callback(
                i,
                len(batches),
            )

    store.set_project_type(project_type)
    store.instructions_path.write_text(
        instructions_text,
        encoding="utf-8",
    )
    store.mark_compiled()

    return {
        "status": "compiled",
        "project_id": store.project_id,
        "source_files": sum(
            1
            for area, _, _ in files
            if area == "source"
        ),
        "workspace_files": sum(
            1
            for area, _, _ in files
            if area == "workspace"
        ),
        "entities": len(
            store.master_metadata().get(
                "entities",
                {},
            )
        ),
        "relationships": len(
            store.relationships()
        ),
        "chunks": len(batches),
        "deterministic_code": deterministic_result or {},
        "skipped_chunks": skipped_chunks,
    }