"""Project creation, selection and project-scoped storage boundaries."""
from pathlib import Path
import os
import shutil
import stat
from ragapp.cognition.store import CognitionStore, slug
from ragapp import settings


def get_project(username, project_id):
    store = CognitionStore(username, project_id)
    store.ensure_initialized()
    return store


def list_projects(username):
    root = Path(settings.PROJECTS_ROOT) / slug(username.lower())
    root.mkdir(parents=True, exist_ok=True)
    return sorted([p.name for p in root.iterdir() if p.is_dir()])


def create_project(username, project_id):
    clean_id = slug(str(project_id).strip())
    if not clean_id:
        raise ValueError("Enter a valid project name.")
    root = Path(settings.PROJECTS_ROOT) / slug(str(username).lower()) / clean_id
    if root.exists():
        raise FileExistsError(f"A project named '{clean_id}' already exists. Choose a different project name.")
    store = CognitionStore(username, clean_id)
    store.ensure_initialized()
    try:
        from ragapp.core.vcs import VCSManager
        VCSManager(store).init()
    except Exception:
        # Project creation remains usable if git is unavailable; approval will
        # surface the VCS error when a commit is required.
        pass
    return store


def _clear_readonly(path):
    """Make a file/directory writable before Windows tries to remove it."""
    try:
        os.chmod(
            path,
            stat.S_IRUSR | stat.S_IWUSR |
            stat.S_IXUSR | stat.S_IRGRP | stat.S_IWGRP |
            stat.S_IXGRP | stat.S_IROTH | stat.S_IWOTH |
            stat.S_IXOTH,
        )
    except OSError:
        pass


def _remove_tree_windows_safe(root):
    """Remove a project tree, including read-only .git objects on Windows."""
    root = Path(root)
    if not root.exists():
        return

    # Git can leave object files/directories read-only. Clear attributes first.
    for current, dirs, files in os.walk(root, topdown=False):
        for name in files:
            _clear_readonly(Path(current) / name)
        for name in dirs:
            _clear_readonly(Path(current) / name)
    _clear_readonly(root)

    def on_error(func, path, exc_info):
        # shutil.rmtree() calls this when unlink/rmdir fails.
        _clear_readonly(path)
        func(path)

    shutil.rmtree(root, onerror=on_error)


def delete_project(username, project_id):
    store = CognitionStore(username, project_id)
    if store.root.exists():
        _remove_tree_windows_safe(store.root)
    return True


def resolve_workspace_path(username, project_id, relative_path):
    store=get_project(username,project_id)
    root=store.workspace.resolve()
    p=(root/relative_path).resolve()
    p.relative_to(root)
    return p
