from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


class VCSManager:
    """Git-backed project history.

    Git is the durable temporal store for project files.  The manager versions
    source/, workspace/, cognition/ and log/.  Chat history is deliberately not
    involved in file recovery.
    """

    VERSIONED_AREAS = ("source", "workspace", "cognition", "log")

    def __init__(self, store):
        self.root = Path(store.root)

    def _run(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, text=True, capture_output=True, check=True
        ).stdout.strip()

    def _run_bytes(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, check=True
        ).stdout

    def init(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / ".git").exists():
            subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "agent@local.invalid"], cwd=self.root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "Cognitive Agent"], cwd=self.root, check=True, capture_output=True, text=True)

    def _has_head(self):
        return subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=self.root,
            text=True, capture_output=True,
        ).returncode == 0

    def _normalise_repo_path(self, path, *, allowed_areas=None):
        value = str(path or "").replace("\\", "/").strip().lstrip("./")
        if not value or value.startswith("/"):
            raise ValueError("A project-relative path is required.")
        parts = Path(value).parts
        if any(p in {"", ".", ".."} for p in parts):
            raise ValueError("Invalid project-relative path.")
        areas = tuple(allowed_areas or self.VERSIONED_AREAS)
        if parts[0] not in areas:
            raise ValueError(f"Path must be under one of: {', '.join(areas)}")
        resolved = (self.root / value).resolve()
        resolved.relative_to(self.root.resolve())
        return Path(value).as_posix()

    def _stage_versioned(self):
        # -A is essential: deletions and renames must be historical events too.
        existing = [area for area in self.VERSIONED_AREAS if (self.root / area).exists()]
        if existing:
            subprocess.run(["git", "add", "-A", "--", *existing], cwd=self.root, check=True)

    def commit(self, message, *, bind_timeline=True):
        """Commit all versioned project areas if they changed."""
        self.init()
        self._stage_versioned()
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=self.root, capture_output=True)
        if staged.returncode == 0:
            return self._run("rev-parse", "HEAD") if self._has_head() else None

        p = subprocess.run(["git", "commit", "-m", str(message)], cwd=self.root, text=True, capture_output=True)
        if p.returncode != 0:
            raise subprocess.CalledProcessError(p.returncode, p.args, p.stdout, p.stderr)
        commit_id = self._run("rev-parse", "HEAD")
        if bind_timeline:
            self._bind_timeline_entries(commit_id, message)
        return self._run("rev-parse", "HEAD")

    def checkpoint(self, message="Pre-change checkpoint"):
        """Persist the exact current versioned tree before a mutation."""
        return self.commit(str(message), bind_timeline=False)

    # Backwards-compatible name used by approval code.
    def backup_authoritative_state(self, message="Pre-state backup"):
        return self.checkpoint(message)

    def _bind_timeline_entries(self, commit_id, message):
        path = self.root / "cognition" / "timeline" / "timeline.json"
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        entries = payload.get("entries")
        if not isinstance(entries, list):
            return False
        bound_at = datetime.now(timezone.utc).isoformat()
        changed = False
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("git_commit"):
                continue
            entry["git_commit"] = commit_id
            entry["git_commit_message"] = str(message or "")
            entry["git_bound_at"] = bound_at
            changed = True
        if not changed:
            return False
        payload["updated_at"] = bound_at
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        subprocess.run(["git", "add", "cognition/timeline/timeline.json"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"Bind timeline to {commit_id[:12]}"],
            cwd=self.root, check=True, capture_output=True, text=True,
        )
        return True

    def log(self, limit=50):
        if not self._has_head():
            return ""
        return self._run("log", f"-{int(limit)}", "--pretty=format:%H%x09%ad%x09%s", "--date=iso")

    def file_history(self, path, limit=50):
        """Return structured history for one versioned path, newest first.

        This is metadata-only by design. Exact contents are fetched separately
        with show_file_at(), avoiding unnecessary model tokens.
        """
        repo_path = self._normalise_repo_path(path)
        if not self._has_head():
            return []
        fmt = "%H%x1f%aI%x1f%an%x1f%ae%x1f%s%x1e"
        out = self._run("log", "--follow", f"-{int(limit)}", f"--format={fmt}", "--", repo_path)
        rows = []
        for record in out.split("\x1e"):
            record = record.strip()
            if not record:
                continue
            fields = record.split("\x1f")
            if len(fields) >= 5:
                rows.append({
                    "commit": fields[0], "timestamp": fields[1], "author": fields[2],
                    "email": fields[3], "message": "\x1f".join(fields[4:]).strip(),
                })
        return rows

    def show_file_at_bytes(self, commit, path):
        repo_path = self._normalise_repo_path(path)
        return self._run_bytes("show", f"{commit}:{repo_path}")

    def show_file_at(self, commit, path, encoding="utf-8"):
        return self.show_file_at_bytes(commit, path).decode(encoding)

    def diff(self, a, b, path=None):
        args = ["diff", a, b]
        if path:
            args.extend(["--", self._normalise_repo_path(path)])
        else:
            args.extend(["--", *self.VERSIONED_AREAS])
        return self._run(*args)

    def restore_file(self, commit, path, *, message=None):
        """Restore a historical file as a new forward-moving revision."""
        repo_path = self._normalise_repo_path(path)
        data = self.show_file_at_bytes(commit, repo_path)
        target = (self.root / repo_path).resolve()
        target.relative_to(self.root.resolve())
        self.checkpoint(f"Pre-restore checkpoint for {repo_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        new_commit = self.commit(
            message or f"Restore {repo_path} from {str(commit)[:12]}",
            bind_timeline=repo_path.startswith("cognition/"),
        )
        return {"path": repo_path, "restored_from": commit, "commit": new_commit}

    # Backwards compatibility: old callers passed a source-relative path.
    def revert_file(self, commit, path):
        repo_path = str(path).replace("\\", "/")
        if not repo_path.startswith("source/"):
            repo_path = f"source/{repo_path}"
        result = self.restore_file(commit, repo_path)
        result["content"] = (self.root / repo_path).read_text(encoding="utf-8")
        return result["content"]
