from __future__ import annotations
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
class VCSManager:
    def __init__(self,store): self.root=store.root
    
    def _run(self,*args): 
        return subprocess.run(['git',*args],cwd=self.root,text=True,capture_output=True,check=True).stdout.strip()
    
    def init(self):
        """Initialize Git metadata without creating an artificial baseline commit."""
        self.root.mkdir(parents=True, exist_ok=True)
        git_dir = self.root / ".git"
        if not git_dir.exists():
            subprocess.run(
                ["git", "init"],
                cwd=self.root,
                check=True,
                capture_output=True,
                text=True,
            )
        # Keep project history self-contained and reproducible even when the
        # host machine has no global Git identity configured.
        subprocess.run(
            ["git", "config", "user.email", "agent@local.invalid"],
            cwd=self.root, check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Cognitive Agent"],
            cwd=self.root, check=True, capture_output=True, text=True,
        )

    def _has_head(self):
        p=subprocess.run(['git','rev-parse','--verify','HEAD'],cwd=self.root,text=True,capture_output=True); return p.returncode==0
    
    def commit(self,message):
        """Commit authoritative project state and bind new timeline entries to it.

        Git cannot embed its own commit hash in the tree being committed. The
        authoritative change therefore receives a first commit, then the
        timeline is enriched with that commit hash in a small follow-up commit.
        The timeline entry points to the first commit, which is the exact tree
        containing the cognition change it describes.
        """
        self.init()
        subprocess.run(['git', 'add', 'source', 'log', 'cognition'], cwd=self.root, check=True)

        # Only authoritative project areas are staged. Untracked runtime/config
        # files outside those areas must not make a pre-state backup fail.
        staged = subprocess.run(
            ['git', 'diff', '--cached', '--quiet'],
            cwd=self.root,
            capture_output=True,
        )
        if staged.returncode == 0:
            return self._run('rev-parse', 'HEAD') if self._has_head() else None

        p = subprocess.run(['git', 'commit', '-m', message], cwd=self.root, text=True, capture_output=True)
        if p.returncode != 0:
            raise subprocess.CalledProcessError(p.returncode, p.args, p.stdout, p.stderr)
        commit_id = self._run('rev-parse', 'HEAD')
        self._bind_timeline_entries(commit_id, message)
        # Return the final HEAD. If timeline binding required the follow-up
        # commit, that commit is now the complete project revision callers can
        # use as the recoverable checkpoint.
        return self._run('rev-parse', 'HEAD')

    def _bind_timeline_entries(self, commit_id, message):
        """Bind currently unbound timeline entries to an already-created commit."""
        path = self.root / 'cognition' / 'timeline' / 'timeline.json'
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return False
        entries = payload.get('entries')
        if not isinstance(entries, list):
            return False

        bound_at = datetime.now(timezone.utc).isoformat()
        changed = False
        for entry in entries:
            if not isinstance(entry, dict) or entry.get('git_commit'):
                continue
            entry['git_commit'] = commit_id
            entry['git_commit_message'] = str(message or '')
            entry['git_bound_at'] = bound_at
            changed = True
        if not changed:
            return False

        payload['updated_at'] = bound_at
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

        # The binding itself is metadata about the already-created authoritative
        # commit. It must be committed separately because a commit cannot contain
        # its own hash. Do not recursively bind the entries again.
        subprocess.run(['git', 'add', 'cognition/timeline/timeline.json'], cwd=self.root, check=True)
        subprocess.run(
            ['git', 'commit', '-m', f'Bind timeline to {commit_id[:12]}'],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    

    def backup_authoritative_state(self, message="Pre-state backup"):
        """Create a Git revision representing the exact state before mutation."""
        return self.commit(str(message))

    def log(self, limit=50):
        if not self._has_head():
            return ''
        return self._run(
            'log',
            f'-{limit}',
            '--pretty=format:%H%x09%ad%x09%s',
            '--date=iso'
        )
    
    def show_file_at(self,commit,path): 
        return self._run('show',f'{commit}:source/{path}')
    
    def diff(self,a,b,path=None): 
        return self._run('diff',a,b,'--',f'source/{path}' if path else 'source')
    
    def revert_file(self,commit,path):
        content=self.show_file_at(commit,path); target=self.root/'source'/path; target.parent.mkdir(parents=True,exist_ok=True); target.write_text(content,encoding='utf-8'); return content
