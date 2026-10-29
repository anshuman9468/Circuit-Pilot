from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ApiError
from .specification import CircuitSpecification


GenerationState = Literal[
    "queued",
    "running",
    "awaiting_clarification",
    "proposed",
    "applied",
    "discarded",
    "failed",
    "cancelled",
    "interrupted",
]
FileRole = Literal["rtl", "testbench", "documentation"]
FileLanguage = Literal["verilog", "systemverilog", "markdown", "text", "json"]

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
ALLOWED_GENERATION_EXTENSIONS = {".v", ".sv", ".vh", ".svh", ".md", ".txt", ".json"}


def _identifier(value: str) -> str:
    value = value.strip()
    if not IDENTIFIER.fullmatch(value):
        raise ValueError("must be a Verilog-style identifier")
    return value


def _relative_path(value: str) -> str:
    value = value.strip()
    if not value or value.startswith("/") or "\\" in value:
        raise ValueError("must be a relative POSIX path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("must not contain empty, dot, or parent path segments")
    if len(parts) > 12:
        raise ValueError("must not contain more than 12 path segments")
    suffix = "." + parts[-1].rsplit(".", 1)[-1].lower() if "." in parts[-1] else ""
    if suffix not in ALLOWED_GENERATION_EXTENSIONS:
        raise ValueError("uses an unsupported text file extension")
    return value


class GenerationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_existing_context: bool = False
    context_file_ids: list[str] = Field(default_factory=list, max_length=50)


class GenerationStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specification_revision: int = Field(ge=1)
    settings: GenerationSettings = Field(default_factory=GenerationSettings)


class ProposedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=255)
    role: FileRole
    language: FileLanguage
    content: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _relative_path(value)

    @model_validator(mode="after")
    def validate_role_path(self):
        if self.role == "rtl" and not self.path.startswith("rtl/"):
            raise ValueError("RTL files must be beneath rtl/")
        if self.role == "testbench" and not self.path.startswith("tb/"):
            raise ValueError("testbench files must be beneath tb/")
        if self.role == "documentation" and not self.path.startswith("docs/"):
            raise ValueError("documentation files must be beneath docs/")
        return self


class SourceManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=255)
    role: Literal["rtl", "testbench"]
    order: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _relative_path(value)


class GenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_module: str
    testbench_top: str
    files: list[ProposedFile] = Field(min_length=1, max_length=100)
    source_manifest: list[SourceManifestEntry] = Field(min_length=2, max_length=200)
    requirement_module_mapping: dict[str, list[str]] = Field(default_factory=dict)
    verification_test_mapping: dict[str, list[str]] = Field(default_factory=dict)
    unmapped_requirements: list[str] = Field(default_factory=list, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=100)
    known_limitations: list[str] = Field(default_factory=list, max_length=100)
    implementation_summary: str = Field(min_length=1, max_length=10_000)

    @field_validator("top_module", "testbench_top")
    @classmethod
    def validate_module_name(cls, value: str) -> str:
        return _identifier(value)

    @model_validator(mode="after")
    def validate_manifest(self):
        paths = [item.path for item in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("generated files must not contain duplicate paths")
        manifest_paths = [item.path for item in self.source_manifest]
        if len(set(manifest_paths)) != len(manifest_paths):
            raise ValueError("source manifest must not contain duplicate paths")
        orders = [item.order for item in self.source_manifest]
        if sorted(orders) != list(range(len(orders))):
            raise ValueError("source manifest order must be a contiguous sequence starting at zero")
        file_by_path = {item.path: item for item in self.files}
        for entry in self.source_manifest:
            file = file_by_path.get(entry.path)
            if file is None:
                raise ValueError(f"source manifest entry {entry.path!r} has no proposed file")
            if file.role != entry.role:
                raise ValueError(f"source manifest role does not match {entry.path!r}")
        required = {item.path for item in self.files if item.role in {"rtl", "testbench"}}
        if required != set(manifest_paths):
            raise ValueError("every RTL and testbench file must appear exactly once in the source manifest")
        if not any(item.role == "rtl" for item in self.files):
            raise ValueError("at least one synthesizable RTL file is required")
        if not any(item.role == "testbench" for item in self.files):
            raise ValueError("a self-checking testbench file is required")
        return self

    @property
    def output_bytes(self) -> int:
        return sum(len(item.content.encode("utf-8")) for item in self.files)


class GenerationClarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    related_field: str
    reason: str
    severity: Literal["blocking", "advisory"] = "blocking"
    expected_answer_type: Literal["text", "choice", "object"] = "text"


class GenerationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str
    project_description: str
    specification: CircuitSpecification
    existing_files: list[dict[str, str]] = Field(default_factory=list, max_length=50)

    def prompt_payload(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2)


class GenerationProviderResponse(BaseModel):
    """Provider output envelope. The provider must return JSON, never executable code outside files."""

    model_config = ConfigDict(extra="forbid")

    result: GenerationResult | None = None
    clarifications: list[GenerationClarification] = Field(default_factory=list, max_length=50)
    raw_text: str = ""
    usage: dict[str, int | None] | None = None
    request_id: str | None = None


def workspace_revision(file_hashes: dict[str, str]) -> str:
    payload = "\n".join(f"{path}\0{file_hashes[path]}" for path in sorted(file_hashes))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_output_limits(result: GenerationResult, max_bytes: int, max_file_bytes: int | None = None) -> None:
    if result.output_bytes > max_bytes:
        raise ApiError(413, "generation_output_too_large", f"The proposed file set exceeds the configured {max_bytes}-byte limit.")
    max_file_bytes = max_file_bytes or max_bytes
    for item in result.files:
        if len(item.content.encode("utf-8")) > max_file_bytes:
            raise ApiError(413, "generation_file_too_large", f"The proposed file {item.path} exceeds the configured file-size limit.")
