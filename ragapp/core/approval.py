from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ragapp.core.vcs import VCSManager
from ragapp.core.instructions import InstructionStore
from ragapp.core.reingest import ReingestPipeline
from ragapp.core.drafts import DraftManager


class ApprovalEngine:
    """Promote a workspace draft into its authoritative project area.

    project/source/ is authoritative project material.
    project/cognition/ is authoritative cognition implementation/state.
    project/workspace/ is temporary review material only.

    A workspace proposal never becomes authoritative at its workspace path.
    Its metadata target determines whether approval updates source/ or
    cognition/.
    """

    def __init__(self, store):
        self.store = store
        self.drafts = DraftManager(store)
        self.vcs = VCSManager(store)
        self.instructions = InstructionStore(store)
        self.reingest = ReingestPipeline(store)

    def _normalise_target(self, target, metadata):
        if not isinstance(target, str):
            raise ValueError("Draft target_file must be a string.")

        target = target.strip().replace("\\", "/")
        while target.startswith("./"):
            target = target[2:]
        if not target or target.startswith("/"):
            raise ValueError("Invalid target path.")

        declared = metadata.get("target_area")
        if declared is not None:
            declared = str(declared).strip().lower()
            if declared not in {"source", "cognition"}:
                raise ValueError(
                    "target_area must be 'source' or 'cognition'."
                )

        if target.startswith("workspace/"):
            raise ValueError(
                "workspace/ is a temporary draft area and cannot be an "
                "approval destination. Set target_file to the authoritative "
                "source/... or cognition/... path."
            )

        if target == "cognition" or target.startswith("cognition/"):
            area = "cognition"
            relative = target[len("cognition/"):] if target != "cognition" else ""
        elif target == "source" or target.startswith("source/"):
            area = "source"
            relative = target[len("source/"):] if target != "source" else ""
        else:
            area = declared or "source"
            relative = target

        if declared and declared != area:
            raise ValueError(
                f"target_area={declared!r} conflicts with target_file={target!r}."
            )

        parts = Path(relative).parts
        if not relative or any(x in {"", ".", ".."} for x in parts):
            raise ValueError("Invalid target path.")
        if parts[0] in {"source", "workspace", "cognition"}:
            raise ValueError(
                f"Target {relative!r} must be relative to {area}/."
            )

        return area, relative

    def _authoritative_path(self, area, relative):
        root = self.store.source if area == "source" else self.store.cognition
        root = root.resolve()
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("Target escapes authoritative project area.") from exc
        return target

    def approve(self, did):
        d = self.drafts.load(did)
        m = d.get("metadata") or {}
        target = m.get("target_file") or m.get("target")
        if not target:
            raise ValueError("Draft has no target_file metadata.")

        area, relative = self._normalise_target(target, m)
        target_path = self._authoritative_path(area, relative)

        if area == "cognition":
            return self._approve_cognition(d, m, target_path, relative)
        return self._approve_source(d, m, target_path, relative)

    def _apply_content(self, d, target_path):
        existed = target_path.exists()
        if existed and not target_path.is_file():
            raise ValueError(f"Approval target is not a file: {target_path}")

        old = target_path.read_text(encoding="utf-8") if existed else None
        mode = str((d.get("metadata") or {}).get("mode", "replace")).lower()
        if mode == "append" and existed:
            new = old + d.get("content", "")
        elif mode == "replace":
            new = d.get("content", "")
        elif mode == "append":
            new = d.get("content", "")
        else:
            raise ValueError("Draft mode must be 'replace' or 'append'.")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(new, encoding="utf-8")
        return existed, old, new

    def _approve_cognition(self, d, m, target_path, relative):
        # Cognition edits update an EXISTING cognition implementation/state entry.
        if not target_path.exists():
            raise FileNotFoundError(
                f"Existing cognition file not found: cognition/{relative}"
            )

        # Entity data files are never a valid draft-approval target. They are
        # mutated ONLY through merge_deltas (STATE_UPDATE), which edits the
        # single named attribute/field and appends a timeline entry, leaving
        # schema_version/sections/relationships/sources/source_projections
        # untouched. A raw content replace here has no concept of "the part
        # that was supposed to change" — it would silently discard everything
        # else in the file. Refuse it outright rather than let it happen.
        if relative == "entities" or relative.startswith("entities/"):
            raise ValueError(
                f"Refusing to approve a direct rewrite of entity data "
                f"(cognition/{relative}). Entity attributes/state must be "
                f"changed via a <STATE_UPDATE> deltas block (merge_deltas), "
                f"which edits only the named field and preserves everything "
                f"else. Draft rejected — no file was modified."
            )

        pre_commit = self.vcs.backup_authoritative_state(
            m.get("change_description", "Pre-state backup before cognition approval")
        )
        existed, old, new = self._apply_content(d, target_path)
        if new == old:
            raise ValueError("Approved cognition change produces no file change.")

        try:
            if target_path.suffix.lower() == ".py":
                compile(new, str(target_path), "exec")
        except Exception:
            target_path.write_text(old, encoding="utf-8")
            raise

        d["status"] = "approved"
        d["approved_at"] = datetime.now(timezone.utc).isoformat()
        d["approved_target"] = {"area": "cognition", "path": relative}
        d["reingest"] = {
            "status": "not_required",
            "reason": "Cognition implementation/state is not source material.",
        }
        self.drafts.save(d)

        desc = m.get("change_description", "Approved cognition change")
        self._write_log(d, desc, "cognition", relative)
        d["pre_state_git_commit"] = pre_commit
        d["commit"] = self.vcs.commit(desc)
        self.drafts.save(d)
        return d

    def _approve_source(self, d, m, target_path, relative):
        existed, old, new = self._apply_content(d, target_path)
        try:
            # Reingest receives SOURCE-relative target information only.
            reingest_result = self.reingest.after_approval(
                target=relative,
                metadata=m,
            )   
        except Exception:
            if existed:
                target_path.write_text(old, encoding="utf-8")
            elif target_path.exists():
                target_path.unlink()
            raise
        # Approval succeeded. The workspace copy was only staging material.
        # Remove it so the authoritative copy exists only under /source.
        workspace_root = self.store.workspace.resolve()
        workspace_path = (workspace_root / relative).resolve()
        try:
            workspace_path.relative_to(workspace_root)
        except ValueError:
            workspace_path = None
        if workspace_path is not None and workspace_path.is_file():
            workspace_path.unlink()
            # Remove empty parent directories left by the staged file.
            parent = workspace_path.parent
            while parent != workspace_root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        d["status"] = "approved"
        d["approved_at"] = datetime.now(timezone.utc).isoformat()
        d["approved_target"] = {"area": "source", "path": relative}
        d["reingest"] = reingest_result
        self.drafts.save(d)
        desc = m.get("change_description", "Approved agent change")
        self._write_log(d, desc, "source", relative)
        d["commit"] = self.vcs.commit(desc)
        self.drafts.save(d)
        return d

    def _write_log(self, d, desc, area, relative):
        self.store.log.mkdir(parents=True, exist_ok=True)
        path = self.store.log / f"{d['id']}.md"
        path.write_text(
            f"# {desc}\n\n"
            f"Draft: {d['id']}\n"
            f"Target: {area}/{relative}\n\n"
            f"{d.get('content', '')}\n",
            encoding="utf-8",
        )
        d["log_file"] = str(path.relative_to(self.store.root))

    def reject(self, did, reason):
        d = self.drafts.load(did)
        target = (d.get("metadata") or {}).get("target_entity_id")
        iid = self.instructions.add(
            reason or ("Rejected proposal: " + d.get("content", "")[:500]),
            scope="file_tagged" if target else "situational",
            tagged_entity_id=target,
            origin="rejection",
        )
        d["status"] = "rejected"
        d["rejection_instruction_id"] = iid
        self.drafts.save(d)
        return d