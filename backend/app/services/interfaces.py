"""Small seams for the later Meta-agent phase.

These are intentionally protocols only. Phase 1 does not invoke a model, run tools,
or stream agent events.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ModelClient(Protocol):
    async def complete(self, prompt: str) -> str: ...


class AgentOrchestrator(Protocol):
    async def plan(self, project_id: str, workspace: Path) -> None: ...


class ToolRunner(Protocol):
    async def run(self, command: list[str], cwd: Path) -> tuple[int, str, str]: ...


class EventStream(Protocol):
    async def publish(self, project_id: str, event_type: str, payload: dict) -> None: ...

