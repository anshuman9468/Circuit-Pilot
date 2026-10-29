from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .specification import CircuitSpecification, PlannerProposal, ValidationReport
from .generation import GenerationClarification, GenerationResult


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
    provider: str = "Meta"
    fallback_configured: bool = False


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


class SpecificationSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    specification: CircuitSpecification


class SpecificationValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specification: CircuitSpecification


class SpecificationReadyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class ClarificationAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    answer: Any


class SpecificationResponse(BaseModel):
    specification: CircuitSpecification
    validation: ValidationReport
    status: str
    current_revision: int
    ready_revision: int | None = None


class SpecificationRevisionSummary(BaseModel):
    revision: int
    status: str
    schema_version: str
    created_at: str
    updated_at: str
    ready_at: str | None = None


class SpecificationRevisionResponse(SpecificationResponse):
    revision_id: str


class SpecificationExportResponse(BaseModel):
    specification: CircuitSpecification
    validation: ValidationReport
    exported_at: str


class SpecificationPlannerConfigResponse(BaseModel):
    enabled: bool
    configured: bool
    status: str
    provider: str
    model: str
    message: str


class SpecificationPlannerStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    instruction: str = Field(default="", max_length=12_000)


class SpecificationPlannerEventResponse(BaseModel):
    id: str
    event_type: str
    summary: str
    metadata: dict[str, Any] | None = None
    created_at: str


class SpecificationPlannerResponse(BaseModel):
    id: str
    project_id: str
    base_revision: int
    user_instruction: str
    state: str
    model: str
    rounds: int
    correction_attempts: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    cancel_requested: bool
    proposal: PlannerProposal | None = None
    validation: ValidationReport | None = None
    current_revision: int | None = None
    accepted_revision: int | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    accepted_at: str | None = None
    events: list[SpecificationPlannerEventResponse] = Field(default_factory=list)


class SpecificationPlannerSummaryResponse(BaseModel):
    id: str
    project_id: str
    base_revision: int
    state: str
    model: str
    rounds: int
    proposal_available: bool
    created_at: str
    updated_at: str


class SpecificationPlannerAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class GenerationConfigResponse(BaseModel):
    enabled: bool
    configured: bool
    status: str
    model: str
    message: str


class GenerationEventResponse(BaseModel):
    id: str
    event_type: str
    summary: str
    metadata: dict[str, Any] | None = None
    created_at: str


class GenerationFileDiffResponse(BaseModel):
    path: str
    role: str
    change_type: str
    existing_content: str | None = None
    proposed_content: str


class GenerationProposalSummaryResponse(BaseModel):
    proposal_id: str
    generation_id: str
    status: str
    specification_revision: int
    top_module: str
    testbench_top: str
    implementation_summary: str
    created_at: str
    updated_at: str


class GenerationProposalResponse(BaseModel):
    proposal_id: str
    generation_id: str
    project_id: str
    status: str
    specification_revision: int
    baseline_revision: str
    result: GenerationResult
    files: list[GenerationFileDiffResponse]
    assumptions: list[str]
    unresolved_questions: list[str]
    known_limitations: list[str]
    backup_count: int
    created_at: str
    updated_at: str


class GenerationResponse(BaseModel):
    id: str
    project_id: str
    specification_revision: int
    state: str
    model: str
    rounds: int
    correction_attempts: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    cancel_requested: bool
    clarification: list[GenerationClarification] = Field(default_factory=list)
    proposal_id: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    events: list[GenerationEventResponse] = Field(default_factory=list)
