from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

