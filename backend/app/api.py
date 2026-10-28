from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import ApiError, not_found
from .models import Project, ProjectEvent, ProjectFile, Run, utc_now
from .schemas import (
    ActivityEventResponse,
    FileCreate,
    FileResponse,
    FileSummary,
    FileUpdate,
    HealthResponse,
    ProjectCreate,
    ProjectResponse,
    ProjectSummary,
    ProjectUpdate,
    RequirementsResponse,
    RequirementsUpdate,
    ResultsResponse,
    RunResponse,
)
from .services.workspace import WorkspaceService


router = APIRouter(prefix="/api")


def get_session(request: Request):
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        yield session


def workspace(request: Request) -> WorkspaceService:
    return request.app.state.workspace


def iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def get_project(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise not_found("Project")
    return project


def project_summary(project: Project) -> ProjectSummary:
    return ProjectSummary(
        id=project.id,
        name=project.name,
        description=project.description,
        created_at=iso(project.created_at),
        updated_at=iso(project.updated_at),
    )


def project_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        **project_summary(project).model_dump(),
        requirements=project.requirements,
    )


def add_event(session: Session, project_id: str, event_type: str, summary: str, metadata: dict | None = None):
    session.add(
        ProjectEvent(
            project_id=project_id,
            event_type=event_type,
            summary=summary,
            metadata_json=metadata,
        )
    )


def touch_project(project: Project) -> None:
    # File mutations are project activity too, so the sidebar's deterministic
    # recency ordering follows the latest saved work across all project data.
    project.updated_at = utc_now()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="circuitpilot-backend")


@router.get("/projects", response_model=list[ProjectSummary])
def list_projects(session: Session = Depends(get_session)) -> list[ProjectSummary]:
    projects = session.scalars(select(Project).order_by(Project.updated_at.desc(), Project.id.asc())).all()
    return [project_summary(project) for project in projects]


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    request: Request,
    session: Session = Depends(get_session),
) -> ProjectResponse:
    if len(payload.requirements.encode("utf-8")) > request.app.state.settings.max_requirements_size:
        raise ApiError(413, "requirements_too_large", "Requirements are larger than the configured limit.")
    project = Project(name=payload.name, description=payload.description, requirements=payload.requirements)
    session.add(project)
    session.flush()
    try:
        workspace(request).ensure_project(project.id)
        add_event(session, project.id, "project.created", "Project created")
        session.commit()
        session.refresh(project)
    except Exception:
        session.rollback()
        project_dir = workspace(request).project_dir(project.id)
        if project_dir.exists():
            shutil.rmtree(project_dir)
        raise
    return project_response(project)


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def retrieve_project(project_id: str, session: Session = Depends(get_session)) -> ProjectResponse:
    return project_response(get_project(session, project_id))


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    session: Session = Depends(get_session),
) -> ProjectResponse:
    project = get_project(session, project_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes:
        for key, value in changes.items():
            setattr(project, key, value)
        add_event(session, project.id, "project.updated", "Project details updated", {"fields": list(changes)})
        session.commit()
        session.refresh(project)
    return project_response(project)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    session: Session = Depends(get_session),
    request: Request = None,
):
    get_project(session, project_id)
    workspace_service = workspace(request)
    tombstone = workspace_service.remove_project(project_id)
    try:
        session.query(Project).filter(Project.id == project_id).delete(synchronize_session=False)
        session.commit()
    except Exception:
        session.rollback()
        if tombstone and tombstone.exists():
            workspace_service.restore_project(project_id, tombstone)
        raise
    if tombstone and tombstone.exists():
        shutil.rmtree(tombstone)
    return None


@router.get("/projects/{project_id}/requirements", response_model=RequirementsResponse)
def get_requirements(project_id: str, session: Session = Depends(get_session)) -> RequirementsResponse:
    project = get_project(session, project_id)
    return RequirementsResponse(project_id=project.id, content=project.requirements, updated_at=iso(project.updated_at))


@router.put("/projects/{project_id}/requirements", response_model=RequirementsResponse)
def update_requirements(
    project_id: str,
    payload: RequirementsUpdate,
    request: Request,
    session: Session = Depends(get_session),
) -> RequirementsResponse:
    project = get_project(session, project_id)
    if len(payload.content.encode("utf-8")) > request.app.state.settings.max_requirements_size:
        raise ApiError(413, "requirements_too_large", "Requirements are larger than the configured limit.")
    project.requirements = payload.content
    add_event(session, project.id, "requirements.updated", "Circuit requirements saved")
    session.commit()
    session.refresh(project)
    return RequirementsResponse(project_id=project.id, content=project.requirements, updated_at=iso(project.updated_at))


@router.get("/projects/{project_id}/files", response_model=list[FileSummary])
def list_files(project_id: str, session: Session = Depends(get_session)) -> list[FileSummary]:
    get_project(session, project_id)
    files = session.scalars(
        select(ProjectFile).where(ProjectFile.project_id == project_id).order_by(ProjectFile.path.asc())
    ).all()
    return [file_summary(file) for file in files]


def file_summary(file: ProjectFile) -> FileSummary:
    return FileSummary(
        id=file.id,
        path=file.path,
        size_bytes=file.size_bytes,
        created_at=iso(file.created_at),
        updated_at=iso(file.updated_at),
    )


@router.post("/projects/{project_id}/files", response_model=FileResponse, status_code=status.HTTP_201_CREATED)
def create_file(
    project_id: str,
    payload: FileCreate,
    request: Request,
    session: Session = Depends(get_session),
) -> FileResponse:
    get_project(session, project_id)
    service = workspace(request)
    service.safe_path(project_id, payload.path)
    duplicate = session.scalar(
        select(ProjectFile).where(ProjectFile.project_id == project_id, ProjectFile.path == payload.path)
    )
    if duplicate:
        raise ApiError(409, "duplicate_file", "A file with that path already exists.")
    size = service.write(project_id, payload.path, payload.content)
    file = ProjectFile(project_id=project_id, path=payload.path, size_bytes=size)
    project = get_project(session, project_id)
    touch_project(project)
    session.add(file)
    add_event(session, project_id, "file.created", f"Created {payload.path}", {"path": payload.path})
    try:
        session.commit()
        session.refresh(file)
    except IntegrityError as exc:
        session.rollback()
        try:
            service.delete(project_id, payload.path)
        except ApiError:
            pass
        raise ApiError(409, "duplicate_file", "A file with that path already exists.") from exc
    return FileResponse(**file_summary(file).model_dump(), content=payload.content)


@router.get("/projects/{project_id}/files/{file_id}", response_model=FileResponse)
def retrieve_file(
    project_id: str,
    file_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> FileResponse:
    get_project(session, project_id)
    file = session.scalar(select(ProjectFile).where(ProjectFile.id == file_id, ProjectFile.project_id == project_id))
    if file is None:
        raise not_found("File")
    content = workspace(request).read(project_id, file.path)
    return FileResponse(**file_summary(file).model_dump(), content=content)


@router.patch("/projects/{project_id}/files/{file_id}", response_model=FileResponse)
def update_file(
    project_id: str,
    file_id: str,
    payload: FileUpdate,
    request: Request,
    session: Session = Depends(get_session),
) -> FileResponse:
    get_project(session, project_id)
    file = session.scalar(select(ProjectFile).where(ProjectFile.id == file_id, ProjectFile.project_id == project_id))
    if file is None:
        raise not_found("File")
    parsed = payload
    service = workspace(request)
    old_path = file.path
    old_content = service.read(project_id, old_path)
    new_path = parsed.path or old_path
    path_changed = new_path != old_path
    if path_changed:
        service.safe_path(project_id, new_path)
        duplicate = session.scalar(
            select(ProjectFile).where(
                ProjectFile.project_id == project_id,
                ProjectFile.path == new_path,
                ProjectFile.id != file.id,
            )
        )
        if duplicate:
            raise ApiError(409, "duplicate_file", "A file with that path already exists.")
    new_content = parsed.content if parsed.content is not None else old_content
    if path_changed:
        service.rename(project_id, old_path, new_path)
    try:
        if parsed.content is not None:
            size = service.write(project_id, new_path, new_content)
        else:
            size = len(new_content.encode("utf-8"))
        file.path = new_path
        file.size_bytes = size
        touch_project(get_project(session, project_id))
        event_type = "file.renamed" if path_changed and parsed.content is None else "file.updated"
        summary = f"Renamed {old_path} to {new_path}" if path_changed and parsed.content is None else f"Saved {new_path}"
        add_event(session, project_id, event_type, summary, {"path": new_path, "previous_path": old_path} if path_changed else {"path": new_path})
        session.commit()
        session.refresh(file)
    except Exception:
        session.rollback()
        if path_changed:
            try:
                service.rename(project_id, new_path, old_path)
            except Exception:
                pass
        if parsed.content is not None:
            service.write(project_id, old_path, old_content)
        raise
    return FileResponse(**file_summary(file).model_dump(), content=new_content)


@router.delete("/projects/{project_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(
    project_id: str,
    file_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    get_project(session, project_id)
    file = session.scalar(select(ProjectFile).where(ProjectFile.id == file_id, ProjectFile.project_id == project_id))
    if file is None:
        raise not_found("File")
    service = workspace(request)
    content = service.read(project_id, file.path)
    path = file.path
    service.delete(project_id, path)
    touch_project(get_project(session, project_id))
    session.delete(file)
    add_event(session, project_id, "file.deleted", f"Deleted {path}", {"path": path})
    try:
        session.commit()
    except Exception:
        session.rollback()
        service.write(project_id, path, content)
        raise
    return None


@router.get("/projects/{project_id}/activity", response_model=list[ActivityEventResponse])
def list_activity(project_id: str, session: Session = Depends(get_session)) -> list[ActivityEventResponse]:
    get_project(session, project_id)
    events = session.scalars(
        select(ProjectEvent)
        .where(ProjectEvent.project_id == project_id)
        .order_by(ProjectEvent.created_at.desc(), ProjectEvent.id.desc())
    ).all()
    return [
        ActivityEventResponse(
            id=event.id,
            event_type=event.event_type,
            summary=event.summary,
            metadata=event.metadata_json,
            created_at=iso(event.created_at),
        )
        for event in events
    ]


@router.get("/projects/{project_id}/runs", response_model=list[RunResponse])
def list_runs(project_id: str, session: Session = Depends(get_session)) -> list[RunResponse]:
    get_project(session, project_id)
    runs = session.scalars(select(Run).where(Run.project_id == project_id).order_by(Run.created_at.desc())).all()
    return [
        RunResponse(id=run.id, status=run.status, created_at=iso(run.created_at), completed_at=iso(run.completed_at) if run.completed_at else None)
        for run in runs
    ]


@router.get("/projects/{project_id}/results", response_model=ResultsResponse)
def get_results(project_id: str, session: Session = Depends(get_session)) -> ResultsResponse:
    get_project(session, project_id)
    return ResultsResponse(
        available=False,
        message="Simulation and synthesis results will be available after the Meta-agent execution phase is connected.",
        runs=[],
    )


def validation_error_response(request: Request, exc) -> JSONResponse:
    details = [{"location": error.get("loc", []), "message": error.get("msg", "Invalid value")} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": "Request validation failed.", "details": details}})
