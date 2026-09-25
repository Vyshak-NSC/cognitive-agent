"""Canonical execution contract for every CPA agent invocation.

Frontends must construct AgentRunContext instead of passing an evolving set of
loose run_agent keyword arguments.  Agent resolution and tool policy are
centralized here so Streamlit, API, CLI and future workflow callers cannot
silently diverge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ragapp.core.agents import AgentStore


@dataclass
class AgentRunContext:
    transcript: list[dict]
    tools: list[Any]
    cognition: Any
    project_id: str
    session_id: str | None = None
    agent_id: str | None = None
    on_section: Callable[[dict], None] | None = None
    max_steps: int | None = None
    agent: dict | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.transcript = list(self.transcript or [])
        self.tools = list(self.tools or [])
        self.project_id = str(self.project_id or "").strip()
        if not self.project_id:
            raise ValueError("AgentRunContext.project_id is required")

        if self.agent_id:
            if self.cognition is None:
                raise ValueError("An agent_id requires a project cognition store")
            self.agent = AgentStore(self.cognition).get(self.agent_id)
            if not self.agent:
                raise ValueError(f"Unknown agent: {self.agent_id}")
            if not self.agent.get("enabled", True):
                raise ValueError(f"Agent is disabled: {self.agent_id}")
            self.tools = self._apply_tool_policy(self.tools, self.agent)

    @staticmethod
    def _apply_tool_policy(tools: list[Any], agent: dict) -> list[Any]:
        """Enforce agent capability policy before any schema reaches the model."""
        allowed = {str(x) for x in (agent.get("allowed_tools") or []) if str(x).strip()}
        denied = {str(x) for x in (agent.get("denied_tools") or []) if str(x).strip()}
        out = []
        for tool in tools:
            name = str(getattr(tool, "name", ""))
            if name in denied:
                continue
            if allowed and name not in allowed:
                continue
            out.append(tool)
        return out

    def agent_guidance(self) -> list[str]:
        """Return only the selected agent's compact runtime constraints."""
        if not self.agent:
            return []
        a = self.agent
        lines: list[str] = []
        if a.get("objective"):
            lines.append(f"Active agent objective: {a['objective']}")
        if a.get("description"):
            lines.append(f"Active agent role: {a['description']}")
        for instruction in a.get("instructions") or []:
            text = str(instruction).strip()
            if text:
                lines.append(f"Agent instruction: {text}")
        if a.get("data_sources"):
            lines.append("Agent data-source boundary: " + ", ".join(map(str, a["data_sources"])))
        if a.get("output_targets"):
            lines.append("Agent output-target boundary: " + ", ".join(map(str, a["output_targets"])))
        if a.get("require_mutation_approval", True):
            lines.append("Agent mutations require the normal review/approval path.")
        return lines
