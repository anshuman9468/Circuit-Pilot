"""Interfaces for the provider and later execution phases."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..specification import PlannerContext, PlannerProposal

class ModelClient(Protocol):
    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_completion_tokens: int,
        response_format: dict[str, Any] | None = None,
    ) -> Any: ...

    async def test_connection(self) -> Any: ...


class AgentOrchestrator(Protocol):
    async def plan(self, project_id: str, workspace: Path) -> None: ...


class RequirementsPlanner(Protocol):
    """Provider-independent boundary for structured specification proposals."""

    async def propose(self, context: PlannerContext, correction: str | None = None) -> PlannerProposal: ...


class ToolRunner(Protocol):
    async def run(self, command: list[str], cwd: Path) -> tuple[int, str, str]: ...


class EventStream(Protocol):
    async def publish(self, project_id: str, event_type: str, payload: dict) -> None: ...
