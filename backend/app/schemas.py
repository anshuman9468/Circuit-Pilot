from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_TEXT_CHARS = 262_144


def _clean_required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be empty")
    return value


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    requirements: str = Field(default="", max_length=MAX_TEXT_CHARS)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _clean_required(value)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return _clean_required(value) if value is not None else None


class RequirementsUpdate(BaseModel):
    content: str = Field(max_length=MAX_TEXT_CHARS)


class FileCreate(BaseModel):
    path: str = Field(min_length=1, max_length=255)
    content: str = Field(default="", max_length=MAX_TEXT_CHARS)


class FileUpdate(BaseModel):
    content: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    path: str | None = Field(default=None, min_length=1, max_length=255)


class ProjectSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    created_at: str
    updated_at: str


class ProjectResponse(ProjectSummary):
    requirements: str


class RequirementsResponse(BaseModel):
    project_id: str
    content: str
    updated_at: str


class FileSummary(BaseModel):
    id: str
    path: str
    size_bytes: int
    created_at: str
    updated_at: str


class FileResponse(FileSummary):
    content: str


class ActivityEventResponse(BaseModel):
    id: str
    event_type: str
    summary: str
    metadata: dict[str, Any] | None = None
    created_at: str


class RunResponse(BaseModel):
    id: str
    status: str
    created_at: str
    completed_at: str | None = None


class ResultsResponse(BaseModel):
    available: bool
    message: str
    runs: list[RunResponse]


class HealthResponse(BaseModel):
    status: str
    service: str


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class ModelConfigResponse(BaseModel):
    configured: bool
    status: str
    model: str
    base_url: str
    message: str
    last_error: str | None = None


class ConnectionTestResponse(BaseModel):
    status: str
    model: str
    message: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ConversationMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: str
    run_id: str | None = None


class UsageResponse(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class RunEventResponse(BaseModel):
    id: str
    event_type: str
    summary: str
    metadata: dict[str, Any] | None = None
    created_at: str


class AgentRunResponse(BaseModel):
    id: str
    project_id: str
    status: str
    model: str
    rounds: int
    tool_calls: int
    usage: UsageResponse
    error_code: str | None = None
    error_message: str | None = None
    cancel_requested: bool
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    events: list[RunEventResponse] = Field(default_factory=list)


class ConversationResponse(BaseModel):
    conversation_id: str
    messages: list[ConversationMessageResponse]
    latest_run: AgentRunResponse | None = None


class ReadFileToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=255)
    start_line: int | None = Field(default=None, ge=1, le=100_000)
    end_line: int | None = Field(default=None, ge=1, le=100_000)

    @model_validator(mode="after")
    def validate_range(self):
        if self.start_line is not None and self.end_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self
