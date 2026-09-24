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
            "Restore a project file from a known Git revision. This preserves later history and creates a new forward-moving commit.",
            {"type": "object", "properties": {"area": area, "relative_path": {"type": "string"}, "commit": {"type": "string"}, "message": {"type": "string"}}, "required": ["area", "relative_path", "commit"]},
            lambda area, relative_path, commit, message=None: service.restore(area, relative_path, commit, message),
        ),
        Tool(
            "create_vcs_checkpoint",
            "Persist the current versioned project tree before a requested deterministic operation. Usually unnecessary because normal file mutations checkpoint automatically.",
            {"type": "object", "properties": {"message": {"type": "string"}}, "required": []},
            lambda message="Manual project checkpoint": {"commit": vcs.checkpoint(message)},
        ),
    ]
