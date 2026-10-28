from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import ApiError, not_found
from ..models import Project, ProjectEvent, Specification, SpecificationRevision, utc_now
from ..specification import (
    CircuitSpecification,
    Clarification,
    ModulePlan,
    OpenQuestion,
    Port,
    ResetSignal,
    SPEC_SCHEMA_VERSION,
    ValidationReport,
    VerificationObjective,
    validate_specification,
)


def _json(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def specification_for_project(session: Session, project_id: str) -> Specification | None:
    return session.scalar(select(Specification).where(Specification.project_id == project_id))


def current_revision(session: Session, project_id: str) -> SpecificationRevision | None:
    specification = specification_for_project(session, project_id)
    if specification is None or specification.current_revision_id is None:
        return None
    return session.get(SpecificationRevision, specification.current_revision_id)


def ensure_current(session: Session, project: Project) -> tuple[Specification, SpecificationRevision]:
    specification = specification_for_project(session, project.id)
    if specification is not None and specification.current_revision_id:
        revision = session.get(SpecificationRevision, specification.current_revision_id)
        if revision is not None:
            return specification, revision

    now = utc_now()
    specification = specification or Specification(project_id=project.id, status="draft")
    session.add(specification)
    session.flush()
    draft = CircuitSpecification(
        specification_id=specification.id,
        project_id=project.id,
        schema_version=SPEC_SCHEMA_VERSION,
        revision=1,
        status="draft",
        created_at=now,
        updated_at=now,
        original_request=project.requirements,
    )
    validation = validate_specification(draft)
    revision = SpecificationRevision(
        specification_id=specification.id,
        project_id=project.id,
        revision=1,
        schema_version=SPEC_SCHEMA_VERSION,
        status="draft",
        data_json=draft.model_dump(mode="json"),
        validation_json=validation.model_dump(mode="json"),
        created_at=now,
        updated_at=now,
    )
    session.add(revision)
    session.flush()
    specification.current_revision_id = revision.id
    specification.updated_at = now
    return specification, revision


def parse_revision(revision: SpecificationRevision) -> CircuitSpecification:
    return CircuitSpecification.model_validate(revision.data_json)


def parse_validation(revision: SpecificationRevision) -> ValidationReport:
    return ValidationReport.model_validate(revision.validation_json)


def add_project_event(session: Session, project_id: str, event_type: str, summary: str, metadata: dict | None = None) -> None:
    session.add(
        ProjectEvent(
            project_id=project_id,
            event_type=event_type,
            summary=summary,
            metadata_json=metadata,
        )
    )


def save_draft(
    session: Session,
    *,
    project: Project,
    expected_revision: int,
    incoming: CircuitSpecification,
) -> tuple[Specification, SpecificationRevision, ValidationReport]:
    specification, previous_revision = ensure_current(session, project)
    if expected_revision != previous_revision.revision:
        raise ApiError(
            409,
            "specification_conflict",
            "This specification changed in another tab. Reload the current revision before saving.",
            {"current_revision": previous_revision.revision},
        )
    now = utc_now()
    previous_validation = parse_validation(previous_revision)
    draft = incoming.model_copy(
        update={
            "specification_id": specification.id,
            "project_id": project.id,
            "schema_version": SPEC_SCHEMA_VERSION,
            "revision": previous_revision.revision + 1,
            "status": "draft",
            "created_at": incoming.created_at or now,
            "updated_at": now,
        }
    )
    validation = validate_specification(draft, previous_validation)
    revision = SpecificationRevision(
        specification_id=specification.id,
        project_id=project.id,
        revision=draft.revision,
        schema_version=SPEC_SCHEMA_VERSION,
        status="draft",
        data_json=draft.model_dump(mode="json"),
        validation_json=validation.model_dump(mode="json"),
        created_at=now,
        updated_at=now,
    )
    session.add(revision)
    session.flush()
    # SQLAlchemy assigns the revision id during flush. Set the pointer only
    # after that flush so a new specification can be reopened reliably.
    specification.current_revision_id = revision.id
    specification.status = "draft"
    specification.updated_at = now
    session.flush()
    return specification, revision, validation


def validate_current(
    session: Session,
    *,
    project: Project,
    draft: CircuitSpecification | None = None,
) -> tuple[Specification, SpecificationRevision, CircuitSpecification, ValidationReport]:
    specification, revision = ensure_current(session, project)
    source = draft or parse_revision(revision)
    report = validate_specification(source, parse_validation(revision))
    return specification, revision, source, report


def mark_ready(
    session: Session,
    *,
    project: Project,
    expected_revision: int,
) -> tuple[Specification, SpecificationRevision, ValidationReport]:
    specification, revision = ensure_current(session, project)
    if expected_revision != revision.revision:
        raise ApiError(
            409,
            "specification_conflict",
            "This specification changed in another tab. Reload the current revision before marking it ready.",
            {"current_revision": revision.revision},
        )
    if revision.status == "ready":
        return specification, revision, parse_validation(revision)
    draft = parse_revision(revision)
    validation = validate_specification(draft, parse_validation(revision))
    if not validation.valid:
        raise ApiError(
            422,
            "specification_not_ready",
            "Resolve all blocking specification issues before marking it ready.",
            {"validation": validation.model_dump(mode="json")},
        )
    now = utc_now()
    ready = draft.model_copy(update={"status": "ready", "updated_at": now})
    revision.status = "ready"
    revision.data_json = ready.model_dump(mode="json")
    revision.validation_json = validation.model_dump(mode="json")
    revision.ready_at = now
    revision.updated_at = now
    specification.status = "ready"
    specification.ready_revision = revision.revision
    specification.updated_at = now
    session.flush()
    return specification, revision, validation


def _answer_value(answer: Any) -> Any:
    if isinstance(answer, dict) and "value" in answer:
        return answer["value"]
    return answer


def apply_clarification_answer(
    specification: CircuitSpecification,
    clarification: Clarification,
    answer: Any,
) -> CircuitSpecification:
    value = _answer_value(answer)
    path = clarification.field_path
    if clarification.code in {"port_direction_missing", "port_signedness_unspecified", "port_width_missing", "unknown_width_parameter"}:
        index = int(path.split("[")[1].split("]")[0])
        if not 0 <= index < len(specification.ports):
            raise ApiError(422, "clarification_stale", "That clarification no longer matches the saved specification.")
        ports = list(specification.ports)
        port = ports[index]
        field = path.rsplit(".", 1)[-1]
        ports[index] = port.model_copy(update={field: value, "source": "user"})
        return specification.model_copy(update={"ports": ports})
    if clarification.code == "design_category_missing":
        return specification.model_copy(update={"design_category": value})
    if clarification.code == "reset_intent_missing":
        return specification.model_copy(update={"reset_intent": value})
    if clarification.code == "requirement_uncovered":
        if not isinstance(value, dict):
            raise ApiError(422, "invalid_clarification_answer", "Provide a verification objective object for this clarification.")
        objective = VerificationObjective.model_validate({**value, "requirement_ids": [clarification.related_field, *value.get("requirement_ids", [])]})
        return specification.model_copy(update={"verification_objectives": [*specification.verification_objectives, objective]})
    if clarification.code == "module_plan_missing":
        if not isinstance(value, dict):
            raise ApiError(422, "invalid_clarification_answer", "Provide a module object for this clarification.")
        module = ModulePlan.model_validate(value)
        return specification.model_copy(update={"modules": [*specification.modules, module]})
    if clarification.code == "clock_required":
        if not isinstance(value, dict):
            raise ApiError(422, "invalid_clarification_answer", "Provide a clock domain object for this clarification.")
        from ..specification import ClockDomain

        clock = ClockDomain.model_validate(value)
        return specification.model_copy(update={"clock_domains": [*specification.clock_domains, clock]})
    if clarification.code == "reset_signal_missing":
        if not isinstance(value, dict):
            raise ApiError(422, "invalid_clarification_answer", "Provide a reset signal object for this clarification.")
        reset = ResetSignal.model_validate(value)
        return specification.model_copy(update={"reset_signals": [*specification.reset_signals, reset]})
    raise ApiError(422, "unsupported_clarification", "This clarification must be resolved by editing the structured field directly.")


def answer_clarification(
    session: Session,
    *,
    project: Project,
    expected_revision: int,
    clarification_id: str,
    answer: Any,
) -> tuple[Specification, SpecificationRevision, ValidationReport]:
    specification, revision = ensure_current(session, project)
    if expected_revision != revision.revision:
        raise ApiError(409, "specification_conflict", "This specification changed in another tab. Reload before answering.", {"current_revision": revision.revision})
    report = parse_validation(revision)
    clarification = next((item for item in report.findings if item.id == clarification_id and item.status == "open"), None)
    if clarification is None:
        raise ApiError(409, "clarification_stale", "That clarification is no longer open for the current revision.")
    updated = apply_clarification_answer(parse_revision(revision), clarification, answer)
    return save_draft(session, project=project, expected_revision=expected_revision, incoming=updated)


def revision_list(session: Session, project_id: str) -> list[SpecificationRevision]:
    return session.scalars(
        select(SpecificationRevision)
        .where(SpecificationRevision.project_id == project_id)
        .order_by(SpecificationRevision.revision.desc())
    ).all()
