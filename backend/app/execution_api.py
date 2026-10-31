from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .api import get_project, get_session
from .errors import ApiError, not_found
from .execution import ExecutionManifestResponse, ExecutionRunResult, ExecutionStartRequest, ToolchainStatus
from .models import ArtifactReference, ProjectEvent, ProjectFile, Run
from .services.execution_service import ACTIVE_EXECUTION_STATES, ExecutionService, create_snapshot, execution_result


router = APIRouter(prefix="/api")


def _run(session: Session, project_id: str, run_id: str) -> Run:
    run = session.scalar(select(Run).where(Run.id == run_id, Run.project_id == project_id, Run.kind == "engineering"))
    if run is None:
        raise not_found("Execution run")
    return run


@router.get("/toolchain/status", response_model=ToolchainStatus)
def toolchain_status(request: Request) -> ToolchainStatus:
    return request.app.state.toolchain_runtime.status()


@router.get("/projects/{project_id}/execution/manifest", response_model=ExecutionManifestResponse)
def execution_manifest(project_id: str, request: Request, session: Session = Depends(get_session)) -> ExecutionManifestResponse:
    get_project(session, project_id)
    files = session.scalars(select(ProjectFile).where(ProjectFile.project_id == project_id).order_by(ProjectFile.path.asc())).all()
    extensions = {".v", ".sv", ".vh", ".svh"}
    rtl_paths = [file.path for file in files if Path(file.path).suffix.lower() in extensions and not file.path.startswith("tb/")]
    testbench_paths = [file.path for file in files if Path(file.path).suffix.lower() in extensions and file.path.startswith("tb/")]
    top_candidates: set[str] = set()
    testbench_candidates: set[str] = set()
    workspace = request.app.state.workspace
    for path in rtl_paths:
        try:
            text = workspace.read(project_id, path)
        except Exception:
            continue
        top_candidates.update(re.findall(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)", text))
    for path in testbench_paths:
        try:
            text = workspace.read(project_id, path)
        except Exception:
            continue
        testbench_candidates.update(re.findall(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)", text))
    return ExecutionManifestResponse(
        project_id=project_id,
        rtl_paths=rtl_paths,
        testbench_paths=testbench_paths,
        include_dirs=sorted({str(Path(path).parent) for path in rtl_paths + testbench_paths if "/" in path}),
        top_candidates=sorted(top_candidates),
        testbench_top_candidates=sorted(testbench_candidates),
    )


@router.post("/projects/{project_id}/execution/runs", response_model=ExecutionRunResult, status_code=status.HTTP_202_ACCEPTED)
async def start_execution(project_id: str, payload: ExecutionStartRequest, request: Request, session: Session = Depends(get_session)) -> ExecutionRunResult:
    project = get_project(session, project_id)
    tool_status = request.app.state.toolchain_runtime.status()
    required_tools = {"verilator", "iverilog", "vvp", "yosys"}
    if not tool_status.runtime_available or not tool_status.image_available or not required_tools.issubset(tool_status.tool_versions):
        raise ApiError(503, "toolchain_unavailable", tool_status.message, {"setup_instructions": tool_status.setup_instructions})
    active_project = session.scalar(select(Run).where(Run.project_id == project_id, Run.kind == "engineering", Run.status.in_(ACTIVE_EXECUTION_STATES)))
    if active_project:
        raise ApiError(409, "execution_already_active", "Only one active engineering execution is allowed per project.")
    all_active = session.scalars(select(Run).where(Run.kind == "engineering", Run.status.in_(ACTIVE_EXECUTION_STATES))).all()
    if len(all_active) >= request.app.state.settings.max_active_execution_runs:
        raise ApiError(409, "execution_capacity_reached", "The configured engineering execution limit is currently in use.")
    run_id = uuid.uuid4().hex
    snapshot, _, _ = create_snapshot(
        session, project, run_id, payload, request.app.state.workspace, request.app.state.settings, tool_status.model_dump()
    )
    run = Run(
        id=run_id,
        project_id=project_id,
        status="queued",
        kind="engineering",
        stage=payload.stage,
        specification_revision=snapshot.specification_revision,
        generation_proposal_id=snapshot.generation_proposal_id,
        snapshot_id=snapshot.snapshot_id,
        snapshot_json=snapshot.model_dump(mode="json"),
        request_json=payload.model_dump(mode="json"),
        toolchain_json=tool_status.model_dump(mode="json"),
    )
    session.add(run)
    session.add(ProjectEvent(project_id=project_id, event_type="execution.queued", summary="Engineering execution queued.", metadata_json={"run_id": run_id, "stage": payload.stage}))
    session.flush()
    service = ExecutionService(request.app)
    service.add_event(session, run_id, "run.queued", "Engineering execution queued.")
    session.commit()
    await request.app.state.execution_manager.start(run_id)
    session.refresh(run)
    return execution_result(session, run)


@router.get("/projects/{project_id}/execution/runs", response_model=list[ExecutionRunResult])
def list_execution_runs(project_id: str, session: Session = Depends(get_session)) -> list[ExecutionRunResult]:
    get_project(session, project_id)
    runs = session.scalars(select(Run).where(Run.project_id == project_id, Run.kind == "engineering").order_by(Run.created_at.desc(), Run.id.desc())).all()
    return [execution_result(session, run) for run in runs]


@router.get("/projects/{project_id}/execution/runs/{run_id}", response_model=ExecutionRunResult)
def get_execution_run(project_id: str, run_id: str, session: Session = Depends(get_session)) -> ExecutionRunResult:
    return execution_result(session, _run(session, project_id, run_id))


@router.post("/projects/{project_id}/execution/runs/{run_id}/cancel", response_model=ExecutionRunResult)
async def cancel_execution(project_id: str, run_id: str, request: Request, session: Session = Depends(get_session)) -> ExecutionRunResult:
    run = _run(session, project_id, run_id)
    if run.status not in ACTIVE_EXECUTION_STATES:
        return execution_result(session, run)
    await request.app.state.execution_manager.cancel(run_id)
    session.expire_all()
    return execution_result(session, _run(session, project_id, run_id))


@router.get("/projects/{project_id}/execution/runs/{run_id}/artifacts/{artifact_id}")
def download_artifact(project_id: str, run_id: str, artifact_id: str, request: Request, session: Session = Depends(get_session)):
    run = _run(session, project_id, run_id)
    artifact = session.scalar(select(ArtifactReference).where(ArtifactReference.id == artifact_id, ArtifactReference.run_id == run.id))
    if artifact is None:
        raise not_found("Artifact")
    root = request.app.state.settings.execution_artifact_root.resolve()
    path = (root / artifact.relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ApiError(404, "artifact_not_found", "The requested artifact is not available.") from exc
    if path.is_symlink() or not path.is_file():
        raise ApiError(404, "artifact_not_found", "The requested artifact is not available.")
    return FileResponse(path, media_type=artifact.content_type or "application/octet-stream", filename=artifact.name or "artifact")
