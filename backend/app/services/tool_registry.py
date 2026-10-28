from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import ApiError
from ..models import Project, ProjectFile
from ..schemas import ReadFileToolArgs
from .workspace import WorkspaceService


class EmptyToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


def tool_error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _bounded_text(content: str, max_chars: int) -> tuple[str, bool]:
    if len(content) <= max_chars:
        return content, False
    return content[:max_chars], True


def _iso(value) -> str:
    from datetime import timezone

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ReadOnlyToolRegistry:
    """Project-bound, read-only functions exposed to the model."""

    def __init__(self, max_result_chars: int):
        self.max_result_chars = max_result_chars

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_project_requirements",
                    "description": "Read the saved natural-language circuit requirements for the current project.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_project_files",
                    "description": "List saved text files in the current project without reading their contents.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_project_file",
                    "description": "Read a saved text file from the current project. Use line ranges for long files.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Project-relative path, such as rtl/top.sv"},
                            "start_line": {"type": "integer", "minimum": 1, "description": "Optional 1-based first line."},
                            "end_line": {"type": "integer", "minimum": 1, "description": "Optional inclusive last line."},
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def execute(
        self,
        name: str,
        arguments: Any,
        *,
        project_id: str,
        session: Session,
        workspace: WorkspaceService,
    ) -> dict[str, Any]:
        try:
            if name == "get_project_requirements":
                EmptyToolArgs.model_validate(arguments)
                project = session.get(Project, project_id)
                if project is None:
                    return tool_error("project_not_found", "The current project no longer exists.")
                content, truncated = _bounded_text(project.requirements, self.max_result_chars)
                return {
                    "ok": True,
                    "requirements": content,
                    "truncated": truncated,
                    "notice": "Requirements were truncated; ask for a narrower summary." if truncated else None,
                }
            if name == "list_project_files":
                EmptyToolArgs.model_validate(arguments)
                if session.get(Project, project_id) is None:
                    return tool_error("project_not_found", "The current project no longer exists.")
                files = session.scalars(
                    select(ProjectFile).where(ProjectFile.project_id == project_id).order_by(ProjectFile.path.asc())
                ).all()
                result = {
                    "ok": True,
                    "files": [
                        {"path": file.path, "size_bytes": file.size_bytes, "updated_at": _iso(file.updated_at)}
                        for file in files
                    ],
                }
                return json.loads(json.dumps(result))
            if name == "read_project_file":
                parsed = ReadFileToolArgs.model_validate(arguments)
                # Validate the supplied path before any database lookup so the
                # model cannot probe another path and every response uses the
                # same workspace escape protections as the REST file service.
                workspace.safe_path(project_id, parsed.path)
                file = session.scalar(
                    select(ProjectFile).where(ProjectFile.project_id == project_id, ProjectFile.path == parsed.path)
                )
                if file is None:
                    return tool_error("file_not_found", "That file does not exist in the current project.")
                content = workspace.read(project_id, file.path)
                lines = content.splitlines()
                total_lines = max(1, len(lines))
                start = parsed.start_line or 1
                requested_end = parsed.end_line or total_lines
                end = min(requested_end, start + 999, total_lines)
                selected = lines[start - 1 : end]
                selected_content = "\n".join(selected)
                truncated = end < requested_end
                selected_content, char_truncated = _bounded_text(selected_content, self.max_result_chars)
                truncated = truncated or char_truncated
                return {
                    "ok": True,
                    "path": file.path,
                    "content": selected_content,
                    "start_line": start,
                    "end_line": min(end, start + selected_content.count("\n")),
                    "total_lines": total_lines,
                    "truncated": truncated,
                    "notice": "Output was truncated; request a smaller line range." if truncated else None,
                }
            return tool_error("unknown_tool", "That tool is not available in CircuitPilot Phase 2.")
        except ValidationError as exc:
            return tool_error("invalid_arguments", self._validation_message(exc))
        except ApiError as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            return tool_error(str(detail.get("code", "tool_failed")), str(detail.get("message", "The tool could not read project data.")))
        except (OSError, UnicodeError):
            return tool_error("tool_failed", "The project file could not be read as bounded UTF-8 text.")

    @staticmethod
    def _validation_message(exc: ValidationError) -> str:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(item) for item in first.get("loc", [])) or "arguments"
        return f"Invalid {location}: {first.get('msg', 'check the tool schema.')}"
