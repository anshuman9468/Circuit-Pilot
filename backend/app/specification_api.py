from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .api import get_project, get_session, iso, touch_project
from .schemas import (
    ClarificationAnswerRequest,
    SpecificationExportResponse,
    SpecificationResponse,
    SpecificationRevisionResponse,
    SpecificationRevisionSummary,
    SpecificationReadyRequest,
    SpecificationSaveRequest,
    SpecificationValidateRequest,
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
from .specification import CircuitSpecification, ValidationReport, validate_specification


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
