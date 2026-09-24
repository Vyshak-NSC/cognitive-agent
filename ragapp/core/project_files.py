from __future__ import annotations

import shutil
from pathlib import Path

from ragapp.core.vcs import VCSManager


class ProjectFileService:
    """Single mutation boundary for project source/workspace files.

    All UI/API/agent mutations should come through this service so Git history
    is complete regardless of which frontend initiated the operation.
    """

    AREAS = {"source", "workspace"}

    def __init__(self, store):
        self.store = store
        self.vcs = VCSManager(store)

    def root(self, area):
        if area not in self.AREAS:
            raise ValueError("area must be 'source' or 'workspace'")
        root = self.store.source if area == "source" else self.store.workspace
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()

    def path(self, area, relative=""):
        root = self.root(area)
        value = str(relative or "").replace("\\", "/").strip()
        while value.startswith("./"):
            value = value[2:]
        if value.startswith("/"):
            raise ValueError("Path must be relative to the selected area.")
        p = (root / value).resolve()
        p.relative_to(root)
        return p

    def repo_path(self, area, relative):
        p = self.path(area, relative)
        return p.relative_to(self.store.root.resolve()).as_posix()

    def _checkpoint(self, description):
        return self.vcs.checkpoint(f"Pre-change: {description}")

    def _commit(self, description, *, cognition=False):
        return self.vcs.commit(description, bind_timeline=cognition)

    def create_folder(self, area, relative, description=None):
        p = self.path(area, relative)
        if p.exists():
            raise FileExistsError(relative)
        # Git cannot version an empty directory; checkpoint still protects any
        # dirty pre-state. The directory becomes versioned when it contains a file.
        self._checkpoint(description or f"create {area}/{relative}")
        p.mkdir(parents=True)
        return {"area": area, "path": relative, "status": "created", "commit": None}

    def write_bytes(self, area, relative, data, *, overwrite=False, description=None):
        p = self.path(area, relative)
        if p.exists() and not overwrite:
            raise FileExistsError(relative)
        if p.exists() and not p.is_file():
            raise ValueError("Target is not a file.")
        existed = p.exists()
        desc = description or (("replace" if existed else "create") + f" {area}/{relative}")
        pre = self._checkpoint(desc)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(bytes(data))
        commit = self._commit(desc)
        return {"area": area, "path": relative, "status": "replaced" if existed else "created", "pre_commit": pre, "commit": commit}

    def write_text(self, area, relative, content, *, overwrite=False, encoding="utf-8", description=None):
        return self.write_bytes(area, relative, str(content).encode(encoding), overwrite=overwrite, description=description)

    def write_many(self, area, files, *, overwrite=False, description="batch file write"):
        """Write multiple binary files as one recoverable Git transition."""
        prepared = []
        for relative, data in files:
            p = self.path(area, relative)
            if p.exists() and not overwrite:
                raise FileExistsError(relative)
            if p.exists() and not p.is_file():
                raise ValueError(f"Target is not a file: {relative}")
            prepared.append((relative, p, bytes(data)))
        pre = self._checkpoint(description)
        for _, p, data in prepared:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        commit = self._commit(description)
        return {"status": "written", "count": len(prepared), "pre_commit": pre, "commit": commit}

    def edit_text(self, area, relative, operation, *, old_text=None, new_text=None, content=None, encoding="utf-8", description=None):
        p = self.path(area, relative)
        if not p.is_file():
            raise FileNotFoundError(relative)
        current = p.read_text(encoding=encoding)
        if operation == "replace":
            if old_text is None or new_text is None or old_text not in current:
                raise ValueError("replace requires old_text/new_text and old_text must exist")
            updated = current.replace(old_text, new_text)
        elif operation == "append":
            updated = current + (content or "")
        elif operation == "prepend":
            updated = (content or "") + current
        elif operation == "replace_all":
            updated = content or ""
        else:
            raise ValueError("operation must be replace, append, prepend, or replace_all")
        if updated == current:
            return {"area": area, "path": relative, "status": "unchanged", "commit": self.vcs._run("rev-parse", "HEAD") if self.vcs._has_head() else None}
        return self.write_text(area, relative, updated, overwrite=True, encoding=encoding, description=description or f"edit {area}/{relative}")

    def delete(self, area, relative, description=None):
        p = self.path(area, relative)
        if not p.exists():
            raise FileNotFoundError(relative)
        desc = description or f"delete {area}/{relative}"
        pre = self._checkpoint(desc)
        shutil.rmtree(p) if p.is_dir() else p.unlink()
        commit = self._commit(desc)
        return {"area": area, "path": relative, "status": "deleted", "pre_commit": pre, "commit": commit}

    def move(self, src_area, src_relative, dst_area, dst_relative, description=None):
        src = self.path(src_area, src_relative)
        dst = self.path(dst_area, dst_relative)
        if not src.exists():
            raise FileNotFoundError(src_relative)
        if dst.exists():
            raise FileExistsError(dst_relative)
        if src.is_dir() and (dst == src or src in dst.parents):
            raise ValueError("Cannot move a folder into itself.")
        desc = description or f"move {src_area}/{src_relative} to {dst_area}/{dst_relative}"
        pre = self._checkpoint(desc)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        commit = self._commit(desc)
        return {"source": f"{src_area}/{src_relative}", "destination": f"{dst_area}/{dst_relative}", "status": "moved", "pre_commit": pre, "commit": commit}

    def copy(self, src_area, src_relative, dst_area, dst_relative, description=None):
        src = self.path(src_area, src_relative)
        dst = self.path(dst_area, dst_relative)
        if not src.exists():
            raise FileNotFoundError(src_relative)
        if dst.exists():
            raise FileExistsError(dst_relative)
        desc = description or f"copy {src_area}/{src_relative} to {dst_area}/{dst_relative}"
        pre = self._checkpoint(desc)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)
        commit = self._commit(desc)
        return {"source": f"{src_area}/{src_relative}", "destination": f"{dst_area}/{dst_relative}", "status": "copied", "pre_commit": pre, "commit": commit}

    def history(self, area, relative, limit=50):
        return self.vcs.file_history(self.repo_path(area, relative), limit=limit)

    def show_revision(self, area, relative, commit, *, encoding="utf-8"):
        repo_path = f"{area}/{Path(relative).as_posix()}"
        return self.vcs.show_file_at(commit, repo_path, encoding=encoding)

    def restore(self, area, relative, commit, description=None):
        repo_path = f"{area}/{Path(relative).as_posix()}"
        return self.vcs.restore_file(commit, repo_path, message=description)
