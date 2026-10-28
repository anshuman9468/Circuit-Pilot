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
    ) -> Any: ...

    async def test_connection(self) -> Any: ...


class AgentOrchestrator(Protocol):
    async def plan(self, project_id: str, workspace: Path) -> None: ...


class RequirementsPlanner(Protocol):
    """Future provider-independent boundary; Phase 3 never instantiates it."""

    async def propose(self, context: PlannerContext) -> PlannerProposal: ...


class ToolRunner(Protocol):
    async def run(self, command: list[str], cwd: Path) -> tuple[int, str, str]: ...


class EventStream(Protocol):
    async def publish(self, project_id: str, event_type: str, payload: dict) -> None: ...
