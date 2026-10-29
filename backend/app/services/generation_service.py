from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select

from ..config import Settings
from ..errors import ApiError
from ..generation import (
    GenerationClarification,
    GenerationContext,
    GenerationResult,
    validate_output_limits,
    workspace_revision,
)
from ..models import (
    GenerationBackup,
    GenerationEvent,
    GenerationJob,
    GenerationProposal,
    Project,
    ProjectEvent,
    ProjectFile,
    SpecificationRevision,
    utc_now,
)
from ..specification import CircuitSpecification
from .model_provider import MetaModelProvider, ProviderError, ProviderResponse
from .workspace import WorkspaceService


ACTIVE_GENERATION_STATES = {"queued", "running"}
TERMINAL_GENERATION_STATES = {
    "awaiting_clarification",
    "proposed",
    "applied",
    "discarded",
    "failed",
    "cancelled",
    "interrupted",
}


class GenerationProvider(Protocol):
    async def generate(self, context: GenerationContext, correction: str | None = None) -> "GenerationAttempt": ...


@dataclass(frozen=True)
class GenerationAttempt:
    result: GenerationResult | None
    clarifications: list[GenerationClarification]
    raw_text: str
    usage: dict[str, int | None] | None = None
    request_id: str | None = None


class GenerationContractError(Exception):
    pass


def _generation_instructions() -> str:
    return (
        "You are the CircuitPilot RTL generation provider. Return ONLY one JSON object and no markdown. "
        "Use the supplied ready specification as the source of truth; do not infer missing essential values. "
        "If a faithful design is not possible, return {\"clarifications\":[{\"id\":...,\"related_field\":...,"
        "\"reason\":...,\"severity\":\"blocking\",\"expected_answer_type\":\"text\"}]} instead of inventing defaults. "
        "Otherwise return an object with top_module, testbench_top, files, source_manifest, "
        "requirement_module_mapping, verification_test_mapping, assumptions, unresolved_questions, "
        "known_limitations, and implementation_summary. Files must use rtl/ for synthesizable Verilog, "
        "tb/ for a self-checking testbench, and docs/ for optional documentation. Use only .v, .sv, .vh, "
        ".svh, .md, .txt, or .json files. The source manifest must include every RTL and testbench file in order. "
        "RTL must be synthesizable Verilog/SystemVerilog in a conservative subset: matching specified ports, "
        "widths, signedness, parameters, clocks, and resets; no initial blocks, delays, # timing, PLI, file I/O, "
        "or simulation-only constructs; no external IP. Use appropriate sequential or combinational coding and "
        "comments for non-obvious choices. Do not claim frequency, area, power, fabrication readiness, simulation, "
        "synthesis, or verification success. The testbench must be finite and self-checking, with explicit stimulus, "
        "expected outcomes derived from the specification and verification objectives (not by copying RTL logic), "
        "visible failures, a watchdog, deterministic behavior, and a final success marker only after all checks pass. "
        "Trace tests to requirement and verification-objective IDs in comments or mapping fields."
    )


def _clean_json_text(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return value


def parse_generation_response(response: ProviderResponse) -> GenerationAttempt:
    raw = response.content or ""
    if not raw.strip():
        raise GenerationContractError("The provider returned an empty generation response.")
    try:
        payload = json.loads(_clean_json_text(raw))
    except json.JSONDecodeError as exc:
        raise GenerationContractError("The provider response was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise GenerationContractError("The provider response must be a JSON object.")
    try:
        if "clarifications" in payload and "files" not in payload and "result" not in payload:
            clarifications = [GenerationClarification.model_validate(item) for item in payload["clarifications"]]
            if not clarifications:
                raise GenerationContractError("The provider returned an empty clarification response.")
            return GenerationAttempt(None, clarifications, raw, _usage(response), response.request_id)
        candidate = payload.get("result", payload)
        if not isinstance(candidate, dict):
            raise GenerationContractError("The provider result must be a JSON object.")
        result = GenerationResult.model_validate(candidate)
    except GenerationContractError:
        raise
    except Exception as exc:
        raise GenerationContractError("The provider response did not match the generation contract.") from exc
    return GenerationAttempt(result, [], raw, _usage(response), response.request_id)


def _usage(response: ProviderResponse) -> dict[str, int | None]:
    return {
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "total_tokens": response.usage.total_tokens,
    }


class MetaGenerationProvider:
    """Generation adapter that reuses the existing Meta Chat Completions client."""

    def __init__(self, provider: MetaModelProvider, settings: Settings):
        self.provider = provider
        self.settings = settings
        self.last_model: str | None = None

    async def generate(self, context: GenerationContext, correction: str | None = None) -> GenerationAttempt:
        correction_text = (
            "\nThe previous response failed contract validation. Correct it and return only a valid JSON object. "
            f"Validation issue: {correction}\n"
            if correction
            else ""
        )
        response = await self.provider.complete(
            messages=[
                {"role": "developer", "content": _generation_instructions()},
                {
                    "role": "user",
                    "content": (
                        "<circuitpilot_generation_context>\n"
                        f"{context.prompt_payload()}\n"
                        "</circuitpilot_generation_context>"
                        f"{correction_text}"
                    ),
                },
            ],
            tools=[],
            max_completion_tokens=self.settings.max_completion_tokens,
        )
        self.last_model = getattr(self.provider, "model", None)
        return parse_generation_response(response)


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def workspace_snapshot(
    session,
    project_id: str,
    workspace: WorkspaceService,
    *,
    include_contents: bool = False,
) -> tuple[str, dict[str, dict[str, Any]], dict[str, str]]:
    files = session.scalars(
        select(ProjectFile).where(ProjectFile.project_id == project_id).order_by(ProjectFile.path.asc())
    ).all()
    metadata: dict[str, dict[str, Any]] = {}
    contents: dict[str, str] = {}
    for file in files:
        content = workspace.read(project_id, file.path)
        digest = _sha256(content)
        metadata[file.path] = {"sha256": digest, "size_bytes": len(content.encode("utf-8"))}
        if include_contents:
            contents[file.path] = content
    hashes = {path: item["sha256"] for path, item in metadata.items()}
    return workspace_revision(hashes), metadata, contents


def build_generation_context(
    session,
    project: Project,
    revision: SpecificationRevision,
    settings: dict[str, Any],
    workspace: WorkspaceService,
) -> GenerationContext:
    existing_files: list[dict[str, str]] = []
    if settings.get("include_existing_context"):
        selected_ids = set(settings.get("context_file_ids") or [])
        files = session.scalars(
            select(ProjectFile).where(ProjectFile.project_id == project.id).order_by(ProjectFile.path.asc())
        ).all()
        by_id = {file.id: file for file in files}
        if not selected_ids.issubset(by_id):
            raise ApiError(400, "invalid_context_file", "Every generation context file must belong to this project.")
        for file in files:
            if file.id in selected_ids:
                existing_files.append({"path": file.path, "content": workspace.read(project.id, file.path)})
    return GenerationContext(
        project_name=project.name,
        project_description=project.description,
        specification=CircuitSpecification.model_validate(revision.data_json),
        existing_files=existing_files,
    )


def validate_result_against_spec(result: GenerationResult, specification: CircuitSpecification) -> None:
    top_modules = [module.name for module in specification.modules if module.is_top_level]
    if len(top_modules) != 1:
        raise GenerationContractError("The ready specification does not contain exactly one top-level module.")
    if result.top_module != top_modules[0]:
        raise GenerationContractError(
            f"The proposed top module {result.top_module!r} does not match the specification top module {top_modules[0]!r}."
        )
    module_names = {module.name for module in specification.modules}
    requirement_ids = {item.id for item in specification.functional_requirements}
    objective_ids = {item.id for item in specification.verification_objectives}
    if any(requirement_id not in requirement_ids for requirement_id in result.unmapped_requirements):
        raise GenerationContractError("The generation result marks an unknown requirement as unmapped.")
    for requirement_id, modules in result.requirement_module_mapping.items():
        if requirement_id not in requirement_ids:
            raise GenerationContractError(f"Requirement mapping references unknown requirement {requirement_id!r}.")
        if any(module not in module_names for module in modules):
            raise GenerationContractError(f"Requirement mapping for {requirement_id!r} references an unknown module.")
    for objective_id in result.verification_test_mapping:
        if objective_id not in objective_ids:
            raise GenerationContractError(f"Verification mapping references unknown objective {objective_id!r}.")


def append_generation_event(session, job: GenerationJob, event_type: str, summary: str, metadata: dict | None = None) -> None:
    session.add(
        GenerationEvent(
            generation_id=job.id,
            event_type=event_type,
            summary=summary,
            metadata_json=metadata,
        )
    )


def append_project_event(session, project_id: str, event_type: str, summary: str, metadata: dict | None = None) -> None:
    session.add(ProjectEvent(project_id=project_id, event_type=event_type, summary=summary, metadata_json=metadata))


def _check_cancelled(session, job: GenerationJob) -> None:
    session.refresh(job)
    if job.cancel_requested or job.state not in ACTIVE_GENERATION_STATES:
        raise asyncio.CancelledError


def _finish(job: GenerationJob, state: str) -> None:
    job.state = state
    job.completed_at = utc_now()


def _fail(session, job: GenerationJob, code: str, message: str) -> None:
    job.state = "failed"
    job.error_code = code
    job.error_message = message[:500]
    job.completed_at = utc_now()
    append_generation_event(session, job, "error", message[:255], {"code": code})
    session.commit()


def _add_usage(job: GenerationJob, usage: dict[str, int | None] | None) -> None:
    if not usage:
        return
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(field)
        if value is not None:
            setattr(job, field, (getattr(job, field) or 0) + value)


async def execute_generation(app, generation_id: str) -> None:
    settings: Settings = app.state.settings
    session_factory = app.state.session_factory
    provider: GenerationProvider = app.state.generation_provider
    workspace: WorkspaceService = app.state.workspace
    with session_factory() as session:
        job = session.get(GenerationJob, generation_id)
        if job is None or job.state not in ACTIVE_GENERATION_STATES:
            return
        project = session.get(Project, job.project_id)
        revision = session.scalar(
            select(SpecificationRevision).where(
                SpecificationRevision.project_id == job.project_id,
                SpecificationRevision.revision == job.specification_revision,
            )
        )
        if project is None or revision is None:
            _fail(session, job, "generation_input_missing", "The saved generation input is no longer available.")
            return
        job.state = "running"
        job.started_at = utc_now()
        append_generation_event(session, job, "started", "Generation started from the immutable specification revision")
        session.commit()
        try:
            context = build_generation_context(session, project, revision, job.settings_json, workspace)
            append_generation_event(session, job, "context_ready", "Generation context prepared from saved project data")
            session.commit()
            correction: str | None = None
            max_attempts = min(settings.max_model_rounds, 1 + settings.max_generation_corrections)
            for attempt_number in range(1, max_attempts + 1):
                _check_cancelled(session, job)
                job.rounds = attempt_number
                append_generation_event(session, job, "provider_request", f"Requesting generation proposal (attempt {attempt_number})")
                session.commit()
                started = time.monotonic()
                try:
                    attempt = await provider.generate(context, correction=correction)
                except ProviderError as exc:
                    _fail(session, job, exc.code, exc.message)
                    return
                except GenerationContractError as exc:
                    attempt = GenerationAttempt(None, [], "", None, None)
                    correction = str(exc)
                _check_cancelled(session, job)
                duration_ms = int((time.monotonic() - started) * 1000)
                job.provider_request_id = attempt.request_id
                job.model = getattr(provider, "last_model", job.model)
                _add_usage(job, attempt.usage)
                append_generation_event(session, job, "provider_response", "Received a generation response", {"duration_ms": duration_ms, "attempt": attempt_number, "usage": attempt.usage})
                session.commit()
                if attempt.clarifications:
                    job.clarification_json = [item.model_dump(mode="json") for item in attempt.clarifications]
                    _finish(job, "awaiting_clarification")
                    append_generation_event(session, job, "awaiting_clarification", "Generation needs explicit clarification before proposing files")
                    append_project_event(session, project.id, "generation.awaiting_clarification", "Generation needs clarification", {"generation_id": job.id})
                    session.commit()
                    return
                if attempt.result is None:
                    correction = correction or "The response contained neither a validated result nor usable clarification questions."
                else:
                    try:
                        validate_output_limits(attempt.result, settings.max_generation_output_bytes, settings.max_file_size)
                        validate_result_against_spec(attempt.result, context.specification)
                    except (ApiError, GenerationContractError) as exc:
                        correction = str(exc)
                    else:
                        proposal = GenerationProposal(
                            generation_id=job.id,
                            project_id=project.id,
                            specification_revision=job.specification_revision,
                            baseline_revision=job.baseline_revision,
                            baseline_files_json=job.baseline_files_json,
                            result_json=attempt.result.model_dump(mode="json"),
                        )
                        job.output_json = attempt.result.model_dump(mode="json")
                        job.state = "proposed"
                        job.completed_at = utc_now()
                        session.add(proposal)
                        append_generation_event(session, job, "proposal_ready", "Generated files are staged for review")
                        append_project_event(session, project.id, "generation.proposed", "RTL and testbench proposal is ready for review", {"generation_id": job.id, "specification_revision": job.specification_revision})
                        session.commit()
                        return
                job.correction_attempts += 1
                append_generation_event(session, job, "contract_correction", "The provider response did not match the generation contract", {"attempt": attempt_number, "reason": correction})
                session.commit()
            _fail(session, job, "malformed_generation", "The provider did not produce a valid generation proposal within the configured correction limit.")
        except asyncio.CancelledError:
            # The cancellation endpoint commits through a separate request
            # session. Read fresh state here so an old SQLite snapshot cannot
            # turn an explicit cancellation into a misleading interruption.
            with session_factory() as final_session:
                final_job = final_session.get(GenerationJob, generation_id)
                if final_job and final_job.state not in TERMINAL_GENERATION_STATES:
                    final_job.state = "cancelled" if final_job.cancel_requested else "interrupted"
                    final_job.error_code = "cancelled" if final_job.cancel_requested else "interrupted"
                    final_job.error_message = "Generation cancelled before completion." if final_job.cancel_requested else "Generation stopped before completion."
                    final_job.completed_at = utc_now()
                    append_generation_event(final_session, final_job, final_job.state, final_job.error_message)
                    final_session.commit()
            return
        except Exception:
            job = session.get(GenerationJob, generation_id)
            if job and job.state not in TERMINAL_GENERATION_STATES:
                _fail(session, job, "internal_error", "Generation failed unexpectedly. Retry explicitly.")


class GenerationManager:
    def __init__(self, app):
        self.app = app
        self.tasks: dict[str, asyncio.Task] = {}

    async def start(self, generation_id: str) -> None:
        if generation_id in self.tasks:
            return
        task = asyncio.create_task(execute_generation(self.app, generation_id), name=f"circuitpilot-generation-{generation_id}")
        self.tasks[generation_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(generation_id, None))

    async def cancel(self, generation_id: str) -> bool:
        with self.app.state.session_factory() as session:
            job = session.get(GenerationJob, generation_id)
            if job is None:
                return False
            if job.state in TERMINAL_GENERATION_STATES:
                return False
            job.cancel_requested = True
            job.state = "cancelled"
            job.error_code = "cancelled"
            job.error_message = "Generation cancelled before completion."
            job.completed_at = utc_now()
            append_generation_event(session, job, "cancelled", job.error_message)
            session.commit()
        task = self.tasks.get(generation_id)
        if task and not task.done():
            task.cancel()
        return True


def recover_interrupted_generations(session_factory) -> int:
    recovered = 0
    with session_factory() as session:
        jobs = session.scalars(select(GenerationJob).where(GenerationJob.state.in_(ACTIVE_GENERATION_STATES))).all()
        for job in jobs:
            job.state = "interrupted"
            job.error_code = "interrupted"
            job.error_message = "The backend restarted before generation completed."
            job.completed_at = utc_now()
            append_generation_event(session, job, "interrupted", job.error_message)
            recovered += 1
        if recovered:
            session.commit()
    return recovered


def active_generation_for_project(session, project_id: str) -> GenerationJob | None:
    return session.scalar(
        select(GenerationJob)
        .where(GenerationJob.project_id == project_id, GenerationJob.state.in_(ACTIVE_GENERATION_STATES))
        .order_by(GenerationJob.created_at.desc())
    )


def apply_proposal(session, proposal: GenerationProposal, project: Project, workspace: WorkspaceService, max_output_bytes: int) -> None:
    result = GenerationResult.model_validate(proposal.result_json)
    validate_output_limits(result, max_output_bytes)
    current_revision, current_metadata, _ = workspace_snapshot(session, project.id, workspace)
    if current_revision != proposal.baseline_revision:
        raise ApiError(409, "generation_workspace_conflict", "Project files changed while generation was running. Review or regenerate against the current workspace.")
    current_files = {file.path: file for file in session.scalars(select(ProjectFile).where(ProjectFile.project_id == project.id)).all()}
    changed: list[tuple[str, bool, str | None]] = []
    try:
        for item in result.files:
            old = current_metadata.get(item.path)
            existed = old is not None
            old_content = workspace.read(project.id, item.path) if existed else None
            proposal.backups.append(GenerationBackup(path=item.path, existed=existed, previous_content=old_content))
            workspace.write(project.id, item.path, item.content)
            file = current_files.get(item.path)
            if file is None:
                session.add(ProjectFile(project_id=project.id, path=item.path, size_bytes=len(item.content.encode("utf-8"))))
            else:
                file.size_bytes = len(item.content.encode("utf-8"))
                file.updated_at = utc_now()
            changed.append((item.path, existed, old_content))
        project.updated_at = utc_now()
        proposal.status = "applied"
        proposal.applied_at = utc_now()
        proposal.generation.state = "applied"
        append_generation_event(session, proposal.generation, "applied", "Validated generated files were applied to the project workspace")
        append_project_event(session, project.id, "generation.applied", "Applied generated RTL and testbench proposal", {"proposal_id": proposal.id, "specification_revision": proposal.specification_revision})
        session.commit()
    except Exception:
        session.rollback()
        for path, existed, old_content in reversed(changed):
            try:
                if existed and old_content is not None:
                    workspace.write(project.id, path, old_content)
                else:
                    workspace.delete(project.id, path)
            except Exception:
                # Keep the proposal/backups for recovery diagnostics if the filesystem itself is unavailable.
                pass
        raise


def discard_proposal(session, proposal: GenerationProposal) -> None:
    if proposal.status != "proposed":
        raise ApiError(409, "proposal_not_discardable", "Only a staged proposal can be discarded.")
    proposal.status = "discarded"
    proposal.generation.state = "discarded"
    proposal.generation.completed_at = utc_now()
    append_generation_event(session, proposal.generation, "discarded", "Staged generation proposal discarded without changing project files")
    append_project_event(session, proposal.project_id, "generation.discarded", "Discarded generated file proposal", {"proposal_id": proposal.id})
    session.commit()
