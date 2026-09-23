from __future__ import annotations
import subprocess
from pathlib import Path
class VCSManager:
    def __init__(self,store): self.root=store.root
    
    def _run(self,*args): 
        return subprocess.run(['git',*args],cwd=self.root,text=True,capture_output=True,check=True).stdout.strip()
    
    def init(self):
        self.root.mkdir(parents=True, exist_ok=True)
        git_dir = self.root / ".git"
        if not git_dir.exists():
            subprocess.run(
                ["git", "init"],
                cwd=self.root,
                check=True,
                capture_output=True,
                text=True
            )
        status = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=self.root,
            capture_output=True,
            text=True
        )
        if status.returncode != 0:
            # Keep project history self-contained and reproducible even when
            # the host machine has no global Git identity configured.
            subprocess.run(["git", "config", "user.email", "agent@local.invalid"], cwd=self.root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.name", "Cognitive Agent"], cwd=self.root, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "add", "."],
                cwd=self.root,
                check=True,
                capture_output=True,
                text=True
            )
            staged = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=self.root,
                capture_output=True,
                text=True
            )
            if staged.returncode != 0:
                subprocess.run(
                    ["git", "commit", "-m", "Initial project state"],
                    cwd=self.root,
                    check=True,
                    capture_output=True,
                    text=True
                )
            
    def _has_head(self):
        p=subprocess.run(['git','rev-parse','--verify','HEAD'],cwd=self.root,text=True,capture_output=True); return p.returncode==0
    
    def commit(self,message):
        self.init(); subprocess.run(['git','add','source','log','cognition'],cwd=self.root,check=True)
        p=subprocess.run(['git','commit','-m',message],cwd=self.root,text=True,capture_output=True)
        return self._run('rev-parse','HEAD')
    
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
