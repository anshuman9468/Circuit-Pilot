from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .api import get_project, get_session, iso
from .errors import ApiError, not_found
from .generation import GenerationStartRequest, GenerationResult
from .models import GenerationJob, GenerationProposal, ProjectFile, SpecificationRevision
from .schemas import (
    GenerationConfigResponse,
    GenerationEventResponse,
    GenerationFileDiffResponse,
    GenerationProposalResponse,
    GenerationProposalSummaryResponse,
    GenerationResponse,
)
from .services.generation_service import (
    active_generation_for_project,
    apply_proposal,
    append_project_event,
    build_generation_context,
    discard_proposal,
    workspace_snapshot,
)
from .services.specification_service import parse_revision, parse_validation


router = APIRouter(prefix="/api")


def _config(request: Request) -> GenerationConfigResponse:
    settings = request.app.state.settings
    if not settings.generation_enabled:
        return GenerationConfigResponse(
            enabled=False,
            configured=bool(settings.model_api_key),
            status="disabled",
            model=settings.meta_model,
            message="Live RTL generation is disabled by the backend feature flag. No provider calls will be made.",
        )
    if not settings.model_api_key:
        return GenerationConfigResponse(
            enabled=True,
            configured=False,
            status="not_configured",
            model=settings.meta_model,
            message="Generation is enabled, but the backend-only model key is not configured.",
        )
    return GenerationConfigResponse(
        enabled=True,
        configured=True,
        status="configured",
        model=settings.meta_model,
        message="Live generation is enabled. Generated files are staged for review and are not compiled or executed.",
    )


@router.get("/config/generation", response_model=GenerationConfigResponse)
def generation_config(request: Request) -> GenerationConfigResponse:
    return _config(request)


def _job_response(job: GenerationJob) -> GenerationResponse:
    clarifications = job.clarification_json or []
    return GenerationResponse(
        id=job.id,
        project_id=job.project_id,
        specification_revision=job.specification_revision,
        state=job.state,
        model=job.model,
        rounds=job.rounds,
        correction_attempts=job.correction_attempts,
        prompt_tokens=job.prompt_tokens,
        completion_tokens=job.completion_tokens,
        total_tokens=job.total_tokens,
        error_code=job.error_code,
        error_message=job.error_message,
        cancel_requested=job.cancel_requested,
        clarification=clarifications,
        proposal_id=job.proposal.id if job.proposal else None,
        created_at=iso(job.created_at),
        started_at=iso(job.started_at) if job.started_at else None,
        completed_at=iso(job.completed_at) if job.completed_at else None,
        events=[
            GenerationEventResponse(
                id=event.id,
                event_type=event.event_type,
                summary=event.summary,
                metadata=event.metadata_json,
                created_at=iso(event.created_at),
            )
            for event in job.events
        ],
    )


def _proposal_summary(proposal: GenerationProposal) -> GenerationProposalSummaryResponse:
    result = GenerationResult.model_validate(proposal.result_json)
    return GenerationProposalSummaryResponse(
        proposal_id=proposal.id,
        generation_id=proposal.generation_id,
        status=proposal.status,
        specification_revision=proposal.specification_revision,
        top_module=result.top_module,
        testbench_top=result.testbench_top,
        implementation_summary=result.implementation_summary,
        created_at=iso(proposal.created_at),
        updated_at=iso(proposal.updated_at),
    )


def _proposal_response(request: Request, proposal: GenerationProposal, session: Session) -> GenerationProposalResponse:
    result = GenerationResult.model_validate(proposal.result_json)
    current_paths = {
        file.path
        for file in session.scalars(select(ProjectFile).where(ProjectFile.project_id == proposal.project_id)).all()
    }
    diffs: list[GenerationFileDiffResponse] = []
    for item in result.files:
        existing_content: str | None = None
        if item.path in current_paths:
            existing_content = request.app.state.workspace.read(proposal.project_id, item.path)
        change_type = "added" if existing_content is None else ("unchanged" if existing_content == item.content else "modified")
        diffs.append(
            GenerationFileDiffResponse(
                path=item.path,
                role=item.role,
                change_type=change_type,
                existing_content=existing_content,
                proposed_content=item.content,
            )
        )
    return GenerationProposalResponse(
        proposal_id=proposal.id,
        generation_id=proposal.generation_id,
        project_id=proposal.project_id,
        status=proposal.status,
        specification_revision=proposal.specification_revision,
        baseline_revision=proposal.baseline_revision,
        result=result,
        files=diffs,
        assumptions=result.assumptions,
        unresolved_questions=result.unresolved_questions,
        known_limitations=result.known_limitations,
        backup_count=len(proposal.backups),
        created_at=iso(proposal.created_at),
        updated_at=iso(proposal.updated_at),
    )


def _get_job(session: Session, project_id: str, generation_id: str) -> GenerationJob:
    job = session.scalar(select(GenerationJob).where(GenerationJob.id == generation_id, GenerationJob.project_id == project_id))
    if job is None:
        raise not_found("Generation")
    return job


def _get_proposal(session: Session, project_id: str, proposal_id: str) -> GenerationProposal:
    proposal = session.scalar(select(GenerationProposal).where(GenerationProposal.id == proposal_id, GenerationProposal.project_id == project_id))
    if proposal is None:
        raise not_found("Generation proposal")
    return proposal


@router.post("/projects/{project_id}/generations", response_model=GenerationResponse, status_code=202)
async def start_generation(
    project_id: str,
    payload: GenerationStartRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> GenerationResponse:
    config = _config(request)
    if not config.enabled:
        raise ApiError(409, "generation_disabled", config.message)
    if not config.configured:
        raise ApiError(409, "model_not_configured", config.message)
    project = get_project(session, project_id)
    active = active_generation_for_project(session, project_id)
    if active is not None:
        raise ApiError(409, "generation_already_active", "This project already has an active generation. Wait for it to finish or cancel it.")
    active_total = session.scalar(select(func.count(GenerationJob.id)).where(GenerationJob.state.in_({"queued", "running"}))) or 0
    if active_total >= request.app.state.settings.max_active_runs:
        raise ApiError(409, "active_generation_limit", "The configured simultaneous generation limit has been reached. Wait for an active generation to finish.")
    revision = session.scalar(
        select(SpecificationRevision).where(
            SpecificationRevision.project_id == project_id,
            SpecificationRevision.revision == payload.specification_revision,
        )
    )
    if revision is None:
        raise not_found("Specification revision")
    validation = parse_validation(revision)
    specification = parse_revision(revision)
    if revision.status != "ready" or specification.status != "ready":
        raise ApiError(422, "specification_not_ready", "Generation requires a ready specification revision.")
    if not validation.valid or validation.blocking_count:
        raise ApiError(422, "specification_not_ready", "Resolve blocking specification issues before generation.", {"validation": validation.model_dump(mode="json")})
    top_levels = [module for module in specification.modules if module.is_top_level]
    if len(top_levels) != 1:
        raise ApiError(422, "generation_missing_top_module", "Generation requires exactly one saved top-level module.")
    if not specification.ports or any(not port.name or port.direction == "unspecified" or port.width is None for port in specification.ports):
        raise ApiError(422, "generation_interface_missing", "Generation requires complete top-level port names, directions, and widths.")
    context_settings = payload.settings.model_dump(mode="json")
    # Validate selected files and read them before creating a durable job, so a bad context never becomes a misleading queued run.
    build_generation_context(session, project, revision, context_settings, request.app.state.workspace)
    baseline_revision, baseline_files, _ = workspace_snapshot(session, project_id, request.app.state.workspace)
    job = GenerationJob(
        project_id=project_id,
        specification_revision=payload.specification_revision,
        baseline_revision=baseline_revision,
        baseline_files_json=baseline_files,
        settings_json=context_settings,
        state="queued",
        model=request.app.state.settings.meta_model,
    )
    session.add(job)
    session.flush()
    from .services.generation_service import append_generation_event

    append_generation_event(session, job, "queued", "Generation queued for explicit user request", {"specification_revision": payload.specification_revision})
    append_project_event(session, project_id, "generation.queued", "Queued RTL and testbench generation", {"generation_id": job.id, "specification_revision": payload.specification_revision})
    session.commit()
    session.refresh(job)
    await request.app.state.generation_manager.start(job.id)
    return _job_response(job)


@router.get("/projects/{project_id}/generations", response_model=list[GenerationResponse])
def list_generations(project_id: str, session: Session = Depends(get_session)) -> list[GenerationResponse]:
    get_project(session, project_id)
    jobs = session.scalars(select(GenerationJob).where(GenerationJob.project_id == project_id).order_by(GenerationJob.created_at.desc())).all()
    return [_job_response(job) for job in jobs]


@router.get("/projects/{project_id}/generations/{generation_id}", response_model=GenerationResponse)
def get_generation(project_id: str, generation_id: str, session: Session = Depends(get_session)) -> GenerationResponse:
    get_project(session, project_id)
    return _job_response(_get_job(session, project_id, generation_id))


@router.post("/projects/{project_id}/generations/{generation_id}/cancel", response_model=GenerationResponse)
async def cancel_generation(project_id: str, generation_id: str, request: Request, session: Session = Depends(get_session)) -> GenerationResponse:
    get_project(session, project_id)
    job = _get_job(session, project_id, generation_id)
    if job.state in {"proposed", "applied", "discarded", "failed", "cancelled", "interrupted", "awaiting_clarification"}:
        raise ApiError(409, "generation_not_active", "This generation is no longer active.")
    await request.app.state.generation_manager.cancel(generation_id)
    session.expire_all()
    return _job_response(_get_job(session, project_id, generation_id))


@router.get("/projects/{project_id}/generation-proposals", response_model=list[GenerationProposalSummaryResponse])
def list_generation_proposals(project_id: str, session: Session = Depends(get_session)) -> list[GenerationProposalSummaryResponse]:
    get_project(session, project_id)
    proposals = session.scalars(select(GenerationProposal).where(GenerationProposal.project_id == project_id).order_by(GenerationProposal.created_at.desc())).all()
    return [_proposal_summary(proposal) for proposal in proposals]


@router.get("/projects/{project_id}/generation-proposals/{proposal_id}", response_model=GenerationProposalResponse)
def get_generation_proposal(project_id: str, proposal_id: str, request: Request, session: Session = Depends(get_session)) -> GenerationProposalResponse:
    get_project(session, project_id)
    return _proposal_response(request, _get_proposal(session, project_id, proposal_id), session)


@router.post("/projects/{project_id}/generation-proposals/{proposal_id}/accept", response_model=GenerationProposalResponse)
def accept_generation_proposal(project_id: str, proposal_id: str, request: Request, session: Session = Depends(get_session)) -> GenerationProposalResponse:
    project = get_project(session, project_id)
    proposal = _get_proposal(session, project_id, proposal_id)
    if proposal.status != "proposed":
        raise ApiError(409, "proposal_not_applicable", "Only a staged proposal can be applied.")
    apply_proposal(session, proposal, project, request.app.state.workspace, request.app.state.settings.max_generation_output_bytes)
    session.refresh(proposal)
    return _proposal_response(request, proposal, session)


@router.post("/projects/{project_id}/generation-proposals/{proposal_id}/discard", response_model=GenerationProposalResponse)
def discard_generation_proposal(project_id: str, proposal_id: str, request: Request, session: Session = Depends(get_session)) -> GenerationProposalResponse:
    get_project(session, project_id)
    proposal = _get_proposal(session, project_id, proposal_id)
    discard_proposal(session, proposal)
    session.refresh(proposal)
    return _proposal_response(request, proposal, session)
