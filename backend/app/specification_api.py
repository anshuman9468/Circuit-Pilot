from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .api import get_project, get_session, iso, touch_project
from .errors import ApiError
from .schemas import (
    ClarificationAnswerRequest,
    SpecificationExportResponse,
    SpecificationResponse,
    SpecificationRevisionResponse,
    SpecificationRevisionSummary,
    SpecificationReadyRequest,
    SpecificationSaveRequest,
    SpecificationPlannerAcceptRequest,
    SpecificationPlannerConfigResponse,
    SpecificationPlannerEventResponse,
    SpecificationPlannerResponse,
    SpecificationPlannerStartRequest,
    SpecificationPlannerSummaryResponse,
    SpecificationValidateRequest,
)
from .models import SpecificationPlannerRun
from .services.planner_service import (
    accept_planner_proposal,
    active_planner_for_project,
    append_planner_event,
    append_project_event,
    build_planner_context,
    discard_planner_proposal,
)
from .services.specification_service import (
    add_project_event,
    answer_clarification,
    current_revision,
    ensure_current,
    mark_ready,
    parse_revision,
    parse_validation,
    revision_list,
    save_draft,
    validate_current,
)
from .specification import CircuitSpecification, PlannerProposal, ValidationReport, validate_specification


router = APIRouter(prefix="/api")


def _response(
    specification,
    revision,
    validation: ValidationReport,
    draft: CircuitSpecification | None = None,
) -> SpecificationResponse:
    return SpecificationResponse(
        specification=draft or parse_revision(revision),
        validation=validation,
        status=specification.status,
        current_revision=revision.revision,
        ready_revision=specification.ready_revision,
    )


def _revision_response(specification, revision, current_revision_number: int) -> SpecificationRevisionResponse:
    return SpecificationRevisionResponse(
        revision_id=revision.id,
        specification=parse_revision(revision),
        validation=parse_validation(revision),
        status=revision.status,
        current_revision=current_revision_number,
        ready_revision=specification.ready_revision,
    )


@router.get("/projects/{project_id}/specification", response_model=SpecificationResponse)
def get_specification(project_id: str, session: Session = Depends(get_session)) -> SpecificationResponse:
    project = get_project(session, project_id)
    specification, revision = ensure_current(session, project)
    session.commit()
    return _response(specification, revision, parse_validation(revision))


@router.put("/projects/{project_id}/specification", response_model=SpecificationResponse)
def save_specification(
    project_id: str,
    payload: SpecificationSaveRequest,
    session: Session = Depends(get_session),
) -> SpecificationResponse:
    project = get_project(session, project_id)
    specification, revision, validation = save_draft(
        session,
        project=project,
        expected_revision=payload.expected_revision,
        incoming=payload.specification,
    )
    touch_project(project)
    add_project_event(
        session,
        project_id,
        "specification.saved",
        f"Saved specification draft revision {revision.revision}",
        {"revision": revision.revision, "blocking_count": validation.blocking_count, "advisory_count": validation.advisory_count},
    )
    session.commit()
    session.refresh(specification)
    return _response(specification, revision, validation)


@router.post("/projects/{project_id}/specification/validate", response_model=SpecificationResponse)
def validate_specification_draft(
    project_id: str,
    payload: SpecificationValidateRequest,
    session: Session = Depends(get_session),
) -> SpecificationResponse:
    project = get_project(session, project_id)
    specification, revision = ensure_current(session, project)
    draft = payload.specification.model_copy(update={"project_id": project_id, "specification_id": specification.id})
    validation = validate_specification(draft, parse_validation(revision))
    touch_project(project)
    add_project_event(
        session,
        project_id,
        "specification.validated",
        "Validated specification draft",
        {"blocking_count": validation.blocking_count, "advisory_count": validation.advisory_count},
    )
    session.commit()
    return _response(specification, revision, validation, draft)


@router.get("/projects/{project_id}/specification/revisions", response_model=list[SpecificationRevisionSummary])
def list_specification_revisions(
    project_id: str,
    session: Session = Depends(get_session),
) -> list[SpecificationRevisionSummary]:
    get_project(session, project_id)
    return [
        SpecificationRevisionSummary(
            revision=revision.revision,
            status=revision.status,
            schema_version=revision.schema_version,
            created_at=iso(revision.created_at),
            updated_at=iso(revision.updated_at),
            ready_at=iso(revision.ready_at) if revision.ready_at else None,
        )
        for revision in revision_list(session, project_id)
    ]


@router.get("/projects/{project_id}/specification/revisions/{revision}", response_model=SpecificationRevisionResponse)
def get_specification_revision(
    project_id: str,
    revision: int,
    session: Session = Depends(get_session),
) -> SpecificationRevisionResponse:
    project = get_project(session, project_id)
    target = next((item for item in revision_list(session, project_id) if item.revision == revision), None)
    if target is None:
        from .errors import not_found

        raise not_found("Specification revision")
    specification, _ = ensure_current(session, project)
    current = current_revision(session, project_id)
    return _revision_response(specification, target, current.revision if current else target.revision)


@router.patch("/projects/{project_id}/specification/clarifications/{clarification_id}", response_model=SpecificationResponse)
def answer_specification_clarification(
    project_id: str,
    clarification_id: str,
    payload: ClarificationAnswerRequest,
    session: Session = Depends(get_session),
) -> SpecificationResponse:
    project = get_project(session, project_id)
    specification, revision, validation = answer_clarification(
        session,
        project=project,
        expected_revision=payload.expected_revision,
        clarification_id=clarification_id,
        answer=payload.answer,
    )
    touch_project(project)
    add_project_event(session, project_id, "specification.clarification_answered", "Answered a specification clarification", {"clarification_id": clarification_id, "revision": revision.revision})
    session.commit()
    return _response(specification, revision, validation)


@router.post("/projects/{project_id}/specification/ready", response_model=SpecificationResponse)
def ready_specification(
    project_id: str,
    payload: SpecificationReadyRequest,
    session: Session = Depends(get_session),
) -> SpecificationResponse:
    project = get_project(session, project_id)
    specification, revision, validation = mark_ready(
        session,
        project=project,
        expected_revision=payload.expected_revision,
    )
    touch_project(project)
    add_project_event(session, project_id, "specification.ready", f"Marked specification revision {revision.revision} ready for implementation", {"revision": revision.revision})
    session.commit()
    return _response(specification, revision, validation)


@router.get("/projects/{project_id}/specification/export", response_model=SpecificationExportResponse)
def export_specification(
    project_id: str,
    session: Session = Depends(get_session),
) -> SpecificationExportResponse:
    project = get_project(session, project_id)
    specification, revision = ensure_current(session, project)
    session.commit()
    return SpecificationExportResponse(
        specification=parse_revision(revision),
        validation=parse_validation(revision),
        exported_at=iso(revision.updated_at),
    )


def _planner_config(request: Request) -> SpecificationPlannerConfigResponse:
    settings = request.app.state.settings
    configured = bool(request.app.state.model_provider.configured)
    provider = getattr(request.app.state.model_provider, "provider_label", "Model")
    model = getattr(request.app.state.model_provider, "model", settings.meta_model)
    if not settings.specification_planner_enabled:
        return SpecificationPlannerConfigResponse(
            enabled=False,
            configured=configured,
            status="disabled",
            provider=provider,
            model=model,
            message="Specification planning is disabled by the backend feature flag. No provider calls will be made.",
        )
    if not configured:
        return SpecificationPlannerConfigResponse(
            enabled=True,
            configured=False,
            status="not_configured",
            provider=provider,
            model=model,
            message="Specification planning is enabled, but the backend-only model key is not configured.",
        )
    return SpecificationPlannerConfigResponse(
        enabled=True,
        configured=True,
        status="configured",
        provider=provider,
        model=model,
        message="Specification planning is enabled. Proposals require review before they become saved drafts.",
    )


@router.get("/config/specification-planner", response_model=SpecificationPlannerConfigResponse)
def specification_planner_config(request: Request) -> SpecificationPlannerConfigResponse:
    return _planner_config(request)


def _planner_response(run: SpecificationPlannerRun) -> SpecificationPlannerResponse:
    proposal = None
    validation = None
    if run.proposal_json:
        proposal = PlannerProposal.model_validate(run.proposal_json)
    if run.validation_json:
        validation = ValidationReport.model_validate(run.validation_json)
    return SpecificationPlannerResponse(
        id=run.id,
        project_id=run.project_id,
        base_revision=run.base_revision,
        user_instruction=run.user_instruction,
        state=run.state,
        model=run.model,
        rounds=run.rounds,
        correction_attempts=run.correction_attempts,
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        total_tokens=run.total_tokens,
        error_code=run.error_code,
        error_message=run.error_message,
        cancel_requested=run.cancel_requested,
        proposal=proposal,
        validation=validation,
        current_revision=None,
        accepted_revision=run.accepted_revision,
        created_at=iso(run.created_at),
        started_at=iso(run.started_at) if run.started_at else None,
        completed_at=iso(run.completed_at) if run.completed_at else None,
        accepted_at=iso(run.accepted_at) if run.accepted_at else None,
        events=[
            SpecificationPlannerEventResponse(
                id=event.id,
                event_type=event.event_type,
                summary=event.summary,
                metadata=event.metadata_json,
                created_at=iso(event.created_at),
            )
            for event in run.events
        ],
    )


def _planner_response_with_current(run: SpecificationPlannerRun, session: Session) -> SpecificationPlannerResponse:
    response = _planner_response(run)
    current = current_revision(session, run.project_id)
    return response.model_copy(update={"current_revision": current.revision if current else None})


def _get_planner_run(session: Session, project_id: str, planner_id: str) -> SpecificationPlannerRun:
    run = session.scalar(
        select(SpecificationPlannerRun).where(
            SpecificationPlannerRun.id == planner_id,
            SpecificationPlannerRun.project_id == project_id,
        )
    )
    if run is None:
        from .errors import not_found

        raise not_found("Specification planner proposal")
    return run


@router.post("/projects/{project_id}/specification/planner", response_model=SpecificationPlannerResponse, status_code=202)
async def start_specification_planner(
    project_id: str,
    payload: SpecificationPlannerStartRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> SpecificationPlannerResponse:
    config = _planner_config(request)
    if not config.enabled:
        raise ApiError(409, "specification_planner_disabled", config.message)
    if not config.configured:
        raise ApiError(409, "model_not_configured", config.message)
    project = get_project(session, project_id)
    specification, revision = ensure_current(session, project)
    if payload.expected_revision != revision.revision:
        raise ApiError(409, "specification_conflict", "This specification changed in another tab. Reload before asking the model to plan.", {"current_revision": revision.revision})
    active = active_planner_for_project(session, project_id)
    if active is not None:
        raise ApiError(409, "specification_planner_already_active", "This project already has an active specification planning request.")
    active_total = session.scalar(select(func.count(SpecificationPlannerRun.id)).where(SpecificationPlannerRun.state.in_({"queued", "running"}))) or 0
    if active_total >= request.app.state.settings.max_active_runs:
        raise ApiError(409, "active_planner_limit", "The configured simultaneous model-run limit has been reached.")
    validation = parse_validation(revision)
    context = build_planner_context(
        project,
        revision,
        validation,
        payload.instruction,
        request.app.state.settings.max_context_chars,
    )
    run = SpecificationPlannerRun(
        project_id=project_id,
        base_revision=revision.revision,
        user_instruction=payload.instruction.strip(),
        context_json=context.model_dump(mode="json"),
        state="queued",
        model=getattr(request.app.state.model_provider, "model", request.app.state.settings.meta_model),
    )
    session.add(run)
    session.flush()
    append_planner_event(session, run, "queued", "Specification planning queued for explicit user request", {"base_revision": revision.revision})
    append_project_event(session, project_id, "specification.planner.queued", "Queued model specification planning", {"planner_id": run.id, "base_revision": revision.revision})
    session.commit()
    session.refresh(run)
    await request.app.state.planner_manager.start(run.id)
    return _planner_response_with_current(run, session)


@router.get("/projects/{project_id}/specification/planner", response_model=list[SpecificationPlannerSummaryResponse])
def list_specification_plans(project_id: str, session: Session = Depends(get_session)) -> list[SpecificationPlannerSummaryResponse]:
    get_project(session, project_id)
    runs = session.scalars(
        select(SpecificationPlannerRun)
        .where(SpecificationPlannerRun.project_id == project_id)
        .order_by(SpecificationPlannerRun.created_at.desc())
    ).all()
    return [
        SpecificationPlannerSummaryResponse(
            id=run.id,
            project_id=run.project_id,
            base_revision=run.base_revision,
            state=run.state,
            model=run.model,
            rounds=run.rounds,
            proposal_available=run.proposal_json is not None,
            created_at=iso(run.created_at),
            updated_at=iso(run.updated_at),
        )
        for run in runs
    ]


@router.get("/projects/{project_id}/specification/planner/{planner_id}", response_model=SpecificationPlannerResponse)
def get_specification_plan(project_id: str, planner_id: str, session: Session = Depends(get_session)) -> SpecificationPlannerResponse:
    get_project(session, project_id)
    return _planner_response_with_current(_get_planner_run(session, project_id, planner_id), session)


@router.post("/projects/{project_id}/specification/planner/{planner_id}/cancel", response_model=SpecificationPlannerResponse)
async def cancel_specification_plan(project_id: str, planner_id: str, request: Request, session: Session = Depends(get_session)) -> SpecificationPlannerResponse:
    get_project(session, project_id)
    run = _get_planner_run(session, project_id, planner_id)
    if run.state not in {"queued", "running"}:
        raise ApiError(409, "specification_planner_not_active", "This specification planning request is no longer active.")
    await request.app.state.planner_manager.cancel(planner_id)
    session.expire_all()
    return _planner_response_with_current(_get_planner_run(session, project_id, planner_id), session)


@router.post("/projects/{project_id}/specification/planner/{planner_id}/accept", response_model=SpecificationPlannerResponse)
def accept_specification_plan(
    project_id: str,
    planner_id: str,
    payload: SpecificationPlannerAcceptRequest,
    session: Session = Depends(get_session),
) -> SpecificationPlannerResponse:
    project = get_project(session, project_id)
    run = _get_planner_run(session, project_id, planner_id)
    if payload.expected_revision != run.base_revision:
        raise ApiError(409, "specification_conflict", "The acceptance request does not match the revision used for planning.", {"base_revision": run.base_revision})
    _, revision, _ = accept_planner_proposal(session, run, project)
    session.refresh(run)
    return _planner_response_with_current(run, session).model_copy(update={"accepted_revision": revision.revision})


@router.post("/projects/{project_id}/specification/planner/{planner_id}/discard", response_model=SpecificationPlannerResponse)
def discard_specification_plan(project_id: str, planner_id: str, session: Session = Depends(get_session)) -> SpecificationPlannerResponse:
    get_project(session, project_id)
    run = _get_planner_run(session, project_id, planner_id)
    discard_planner_proposal(session, run)
    session.refresh(run)
    return _planner_response_with_current(run, session)
