from __future__ import annotations

from ragapp.core.project_files import ProjectFileService
from ragapp.core.vcs import VCSManager
from ragapp.tools.definitions import Tool


def build_vcs_tools(store):
    service = ProjectFileService(store)
    vcs = VCSManager(store)
    area = {"type": "string", "enum": ["workspace", "source"]}
    return [
        Tool(
            "list_file_history",
            "List Git revisions for one project file. Use this for requests about previous/older versions. Returns metadata only; fetch exact content only for the needed revision.",
            {"type": "object", "properties": {"area": area, "relative_path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["area", "relative_path"]},
            lambda area, relative_path, limit=20: service.history(area, relative_path, min(max(int(limit), 1), 100)),
        ),
        Tool(
            "show_file_revision",
            "Read the exact historical contents of one file at a known Git revision. Does not use chat history.",
            {"type": "object", "properties": {"area": area, "relative_path": {"type": "string"}, "commit": {"type": "string"}}, "required": ["area", "relative_path", "commit"]},
            lambda area, relative_path, commit: {"area": area, "path": relative_path, "commit": commit, "content": service.show_revision(area, relative_path, commit)},
        ),
        Tool(
            "diff_file_revisions",
            "Compare two Git revisions for a project file.",
            {"type": "object", "properties": {"area": area, "relative_path": {"type": "string"}, "revision_a": {"type": "string"}, "revision_b": {"type": "string"}}, "required": ["area", "relative_path", "revision_a", "revision_b"]},
            lambda area, relative_path, revision_a, revision_b: {"diff": vcs.diff(revision_a, revision_b, f"{area}/{relative_path}")},
        ),
        Tool(
            "restore_file_revision",
            "Restore ONE project file from a known Git revision (forward-moving commit). Do NOT loop this over many files; to undo/revert everything or return the whole project to an earlier state use restore_project_state once.",
            {"type": "object", "properties": {"area": area, "relative_path": {"type": "string"}, "commit": {"type": "string"}, "message": {"type": "string"}}, "required": ["area", "relative_path", "commit"]},
            lambda area, relative_path, commit, message=None: service.restore(area, relative_path, commit, message),
        ),
        Tool(
            "list_project_states",
            "List project-wide restore points (whole-project git states), newest first, with noise commits hidden. Use kind='compile' to see only compile points. Use only if you need a specific commit; 'last_compile' needs no lookup.",
            {"type": "object", "properties": {"limit": {"type": "integer"}, "kind": {"type": "string", "enum": ["all", "compile"]}}, "required": []},
            lambda limit=15, kind="all": {"states": vcs.list_states(limit, kind)},
        ),
        Tool(
            "restore_project_state",
            "UNDO / REVERT / ROLL BACK THE WHOLE PROJECT in ONE deterministic call (source, workspace, cognition, log). target='last_compile' returns to the state right after the most recent compile; or pass a commit id from list_project_states. Forward-moving and recoverable. Call it ONCE, then report its result; never restore files one by one.",
            {"type": "object", "properties": {"target": {"type": "string", "description": "'last_compile' (default) or a commit id."}}, "required": []},
            lambda target="last_compile": vcs.restore_project_state(target),
        ),
        Tool(
            "create_vcs_checkpoint",
            "Persist the current versioned project tree before a requested deterministic operation. Usually unnecessary because normal file mutations checkpoint automatically.",
            {"type": "object", "properties": {"message": {"type": "string"}}, "required": []},
            lambda message="Manual project checkpoint": {"commit": vcs.checkpoint(message)},
        ),
    ]