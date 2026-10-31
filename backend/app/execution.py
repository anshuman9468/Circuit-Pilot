from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


HDL_EXTENSIONS = {".v", ".sv", ".vh", ".svh"}
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
SAFE_PATH = re.compile(r"^[A-Za-z0-9_./$+-]+$")


def validate_identifier(value: str) -> str:
    value = value.strip()
    if not IDENTIFIER.fullmatch(value):
        raise ValueError("must be a valid Verilog identifier")
    return value


def validate_relative_path(value: str) -> str:
    value = value.strip().replace("\\", "/")
    if not value or value.startswith("/") or "//" in value:
        raise ValueError("must be a non-empty relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("path traversal and empty path components are not allowed")
    if not SAFE_PATH.fullmatch(value):
        raise ValueError("path contains unsupported characters")
    if value.lower().endswith((".v", ".sv", ".vh", ".svh")) is False:
        raise ValueError("path must use a supported HDL extension")
    return value


ParameterValue = int | str


class ExecutionStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Literal["lint", "simulation", "synthesis", "checks"] = "checks"
    specification_revision: int | None = Field(default=None, ge=1)
    generation_proposal_id: str | None = Field(default=None, min_length=1, max_length=36)
    rtl_paths: list[str] = Field(min_length=1, max_length=200)
    testbench_paths: list[str] = Field(default_factory=list, max_length=200)
    include_dirs: list[str] = Field(default_factory=list, max_length=50)
    top_module: str
    testbench_top: str | None = None
    parameters: dict[str, ParameterValue] = Field(default_factory=dict, max_length=50)

    @field_validator("rtl_paths", "testbench_paths")
    @classmethod
    def paths(cls, values: list[str]) -> list[str]:
        result = [validate_relative_path(value) for value in values]
        if len(result) != len(set(result)):
            raise ValueError("duplicate source paths are not allowed")
        return result

    @field_validator("include_dirs")
    @classmethod
    def include_paths(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            value = value.strip().replace("\\", "/")
            if not value or value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/")):
                raise ValueError("include directories must be safe relative paths")
            if value not in result:
                result.append(value)
        return result

    @field_validator("top_module", "testbench_top")
    @classmethod
    def identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("parameters")
    @classmethod
    def parameter_names(cls, values: dict[str, ParameterValue]) -> dict[str, ParameterValue]:
        for name, value in values.items():
            validate_identifier(name)
            if isinstance(value, str):
                if not value or len(value) > 80 or not re.fullmatch(r"[A-Za-z0-9_$'xXzZbBoOdDhH_+-]+", value):
                    raise ValueError(f"parameter {name} has an unsupported value")
        return values

    @model_validator(mode="after")
    def stage_requirements(self):
        if self.stage in {"simulation", "checks"} and (not self.testbench_paths or not self.testbench_top):
            raise ValueError("simulation and checks require testbench_paths and testbench_top")
        if self.stage in {"lint", "synthesis"} and self.testbench_paths:
            raise ValueError("lint and synthesis manifests must not include testbench files")
        return self


class SnapshotManifest(BaseModel):
    path: str
    sha256: str
    size_bytes: int
    role: Literal["rtl", "testbench", "include"]


class ExecutionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    run_id: str
    snapshot_id: str
    captured_at: datetime
    specification_revision: int | None = None
    generation_proposal_id: str | None = None
    top_module: str
    testbench_top: str | None = None
    rtl_manifest: list[SnapshotManifest]
    testbench_manifest: list[SnapshotManifest]
    include_manifest: list[SnapshotManifest] = Field(default_factory=list)
    include_dirs: list[str] = Field(default_factory=list)
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)
    workspace_revision: str
    source_bytes: int
    toolchain: dict[str, Any] = Field(default_factory=dict)


class Diagnostic(BaseModel):
    severity: Literal["info", "warning", "error", "unknown"]
    message: str
    raw: str | None = None
    file: str | None = None
    line: int | None = None
    column: int | None = None


class TestbenchReport(BaseModel):
    status: Literal["passed", "failed", "unknown"] = "unknown"
    checks_total: int = 0
    checks_passed: int = 0
    checks_failed: int = 0
    check_ids: list[str] = Field(default_factory=list)
    completion_marker: str | None = None
    limitation: str | None = None


class StageResult(BaseModel):
    stage: str
    status: str
    tool: str
    tool_version: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    exit_code: int | None = None
    termination_reason: str | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    testbench_report: TestbenchReport | None = None
    metrics: dict[str, int | float | str] = Field(default_factory=dict)
    artifact_ids: list[str] = Field(default_factory=list)
    log_artifact_id: str | None = None
    log_truncated: bool = False


class ExecutionEvent(BaseModel):
    id: str
    stage_name: str | None = None
    event_type: str
    summary: str
    metadata: dict[str, Any] | None = None
    created_at: datetime


class ExecutionArtifact(BaseModel):
    id: str
    kind: str
    name: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    content_type: str | None = None
    stage: str | None = None
    created_at: datetime


class ExecutionRunResult(BaseModel):
    id: str
    project_id: str
    status: str
    stage: str
    snapshot_id: str | None = None
    specification_revision: int | None = None
    generation_proposal_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    cancel_requested: bool = False
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    stages: list[StageResult] = Field(default_factory=list)
    events: list[ExecutionEvent] = Field(default_factory=list)
    artifacts: list[ExecutionArtifact] = Field(default_factory=list)


class ToolchainStatus(BaseModel):
    runtime_requested: str
    runtime: str | None = None
    runtime_available: bool
    runtime_version: str | None = None
    image: str
    image_available: bool
    tool_versions: dict[str, str] = Field(default_factory=dict)
    checked_at: datetime
    message: str
    setup_instructions: list[str] = Field(default_factory=list)


class ExecutionManifestResponse(BaseModel):
    project_id: str
    rtl_paths: list[str]
    testbench_paths: list[str]
    include_dirs: list[str]
    top_candidates: list[str]
    testbench_top_candidates: list[str]
