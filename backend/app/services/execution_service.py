from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import ApiError
from ..execution import (
    Diagnostic,
    ExecutionRunResult,
    ExecutionSnapshot,
    ExecutionStartRequest,
    SnapshotManifest,
    StageResult,
    TestbenchReport,
    validate_identifier,
)
from ..models import (
    ArtifactReference,
    GenerationProposal,
    Project,
    ProjectEvent,
    ProjectFile,
    Run,
    RunEvent,
    RunStage,
    SpecificationRevision,
    new_id,
    utc_now,
)
from .toolchain import ContainerRuntime, ProcessResult
from .workspace import ALLOWED_EXTENSIONS, WorkspaceService


EXECUTION_HDL_EXTENSIONS = {".v", ".sv", ".vh", ".svh"}
ACTIVE_EXECUTION_STATES = {"queued", "running"}
TERMINAL_EXECUTION_STATES = {"completed", "failed", "timed_out", "cancelled", "interrupted"}


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _relative_snapshot_path(path: str) -> Path:
    candidate = Path(*path.split("/"))
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ApiError(400, "invalid_snapshot_path", "Execution paths must remain relative to the project workspace.")
    return candidate


def _check_file_path(path: str) -> None:
    suffix = Path(path).suffix.lower()
    if suffix not in EXECUTION_HDL_EXTENSIONS:
        raise ApiError(400, "unsupported_execution_file", "Execution accepts only Verilog/SystemVerilog source files.")


def _manifest_file(session: Session, project_id: str, path: str) -> ProjectFile:
    file = session.scalar(select(ProjectFile).where(ProjectFile.project_id == project_id, ProjectFile.path == path))
    if file is None:
        raise ApiError(400, "source_file_not_found", f"The selected source file {path!r} is not part of this project.")
    return file


def _safe_source_bytes(workspace: WorkspaceService, project_id: str, path: str, max_bytes: int) -> bytes:
    source = workspace.safe_path(project_id, path)
    if source.is_symlink() or not source.is_file():
        raise ApiError(400, "unsafe_source_file", f"The selected source file {path!r} is missing or is not a regular file.")
    try:
        resolved = source.resolve(strict=True)
        resolved.relative_to(workspace.project_dir(project_id).resolve())
    except (OSError, ValueError) as exc:
        raise ApiError(400, "unsafe_source_file", f"The selected source file {path!r} escapes the project workspace.") from exc
    content = source.read_bytes()
    if len(content) > max_bytes:
        raise ApiError(413, "source_too_large", f"Source files must be smaller than {max_bytes} bytes for execution.")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApiError(400, "source_not_text", "HDL inputs must be UTF-8 text.") from exc
    return content


def _copy_source(workspace: WorkspaceService, project_id: str, path: str, destination_root: Path, max_bytes: int) -> tuple[str, int, str]:
    content = _safe_source_bytes(workspace, project_id, path, max_bytes)
    destination = destination_root / _relative_snapshot_path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return path, len(content), sha256_bytes(content)


def validate_execution_reference(session: Session, project_id: str, request: ExecutionStartRequest) -> tuple[SpecificationRevision | None, GenerationProposal | None]:
    revision = None
    proposal = None
    if request.specification_revision is not None:
        revision = session.scalar(
            select(SpecificationRevision).where(
                SpecificationRevision.project_id == project_id,
                SpecificationRevision.revision == request.specification_revision,
            )
        )
        if revision is None:
            raise ApiError(404, "specification_revision_not_found", "The specification revision does not belong to this project.")
        if revision.status != "ready":
            raise ApiError(409, "specification_not_ready", "Execution requires a ready specification revision.")
        validation = revision.validation_json or {}
        if int(validation.get("blocking_count", 0)) > 0:
            raise ApiError(409, "specification_has_blocking_issues", "Resolve blocking specification issues before execution.")
    if request.generation_proposal_id:
        proposal = session.scalar(
            select(GenerationProposal).where(
                GenerationProposal.id == request.generation_proposal_id,
                GenerationProposal.project_id == project_id,
            )
        )
        if proposal is None:
            raise ApiError(404, "generation_proposal_not_found", "The generation proposal does not belong to this project.")
        if proposal.status != "applied":
            raise ApiError(409, "generation_proposal_not_applied", "Only an applied generation proposal can be executed.")
        if request.specification_revision is not None and proposal.specification_revision != request.specification_revision:
            raise ApiError(409, "revision_mismatch", "The generation proposal and specification revision do not match.")
    return revision, proposal


def create_snapshot(
    session: Session,
    project: Project,
    run_id: str,
    request: ExecutionStartRequest,
    workspace: WorkspaceService,
    settings: Settings,
    toolchain_status: dict[str, Any],
) -> tuple[ExecutionSnapshot, Path, Path]:
    revision, proposal = validate_execution_reference(session, project.id, request)
    selected_paths = request.rtl_paths + request.testbench_paths
    if len(selected_paths) > settings.toolchain_max_source_files:
        raise ApiError(413, "too_many_source_files", "The selected source manifest is too large.")
    if len(set(selected_paths)) != len(selected_paths):
        raise ApiError(400, "duplicate_source_file", "A source file may appear only once in the execution manifest.")
    for path in selected_paths:
        _check_file_path(path)
        _manifest_file(session, project.id, path)
    for path in request.include_dirs:
        workspace.safe_directory(project.id, path)

    snapshot_id = new_id()
    snapshot_dir = settings.execution_snapshot_root / run_id
    output_dir = settings.execution_output_root / run_id
    if snapshot_dir.exists() or output_dir.exists():
        raise ApiError(409, "snapshot_exists", "The execution snapshot identifier is already in use.")
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    output_dir.mkdir(parents=True, exist_ok=False)
    # The container runs as a dedicated non-root UID. Give it read access to
    # the immutable snapshot and write access only to this run's scratch area.
    snapshot_dir.chmod(0o755)
    output_dir.chmod(0o777)
    manifests: list[SnapshotManifest] = []
    include_manifests: list[SnapshotManifest] = []
    total_bytes = 0
    try:
        for path, role in [(path, "rtl") for path in request.rtl_paths] + [(path, "testbench") for path in request.testbench_paths]:
            path, size, digest = _copy_source(workspace, project.id, path, snapshot_dir, settings.toolchain_max_source_bytes)
            total_bytes += size
            if total_bytes > settings.toolchain_max_source_bytes:
                raise ApiError(413, "source_too_large", "The complete execution source snapshot is too large.")
            manifests.append(SnapshotManifest(path=path, sha256=digest, size_bytes=size, role=role))
        for include_dir in request.include_dirs:
            source_dir = workspace.safe_directory(project.id, include_dir)
            # Include directories are copied only for HDL files, and symlinks are
            # rejected rather than followed.
            for source in sorted(source_dir.rglob("*")):
                if source.is_symlink():
                    raise ApiError(400, "unsafe_include_directory", "Symlinks are not allowed in include directories.")
                if source.is_file() and source.suffix.lower() in EXECUTION_HDL_EXTENSIONS:
                    relative = source.relative_to(workspace.project_dir(project.id)).as_posix()
                    if relative not in selected_paths:
                        _, size, digest = _copy_source(workspace, project.id, relative, snapshot_dir, settings.toolchain_max_source_bytes)
                        total_bytes += size
                        if total_bytes > settings.toolchain_max_source_bytes:
                            raise ApiError(413, "source_too_large", "The complete execution source snapshot is too large.")
                        include_manifests.append(SnapshotManifest(path=relative, sha256=digest, size_bytes=size, role="include"))
        baseline = {item.path: item.sha256 for item in manifests + include_manifests}
        workspace_revision = _json_hash({"files": baseline, "rtl": request.rtl_paths, "tb": request.testbench_paths})
        snapshot = ExecutionSnapshot(
            project_id=project.id,
            run_id=run_id,
            snapshot_id=snapshot_id,
            captured_at=datetime.now(timezone.utc),
            specification_revision=revision.revision if revision else None,
            generation_proposal_id=proposal.id if proposal else None,
            top_module=request.top_module,
            testbench_top=request.testbench_top,
            rtl_manifest=[item for item in manifests if item.role == "rtl"],
            testbench_manifest=[item for item in manifests if item.role == "testbench"],
            include_manifest=include_manifests,
            include_dirs=request.include_dirs,
            parameters=request.parameters,
            workspace_revision=workspace_revision,
            source_bytes=total_bytes,
            toolchain=toolchain_status,
        )
        (snapshot_dir / "snapshot.json").write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        script = build_yosys_script(snapshot)
        (snapshot_dir / "__circuitpilot").mkdir(parents=True, exist_ok=True)
        (snapshot_dir / "__circuitpilot" / "synthesis.ys").write_text(script, encoding="utf-8")
        return snapshot, snapshot_dir, output_dir
    except Exception:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)
        raise


def _value(value: int | str) -> str:
    return str(value)


def _parameter_args(snapshot: ExecutionSnapshot, prefix: str) -> list[str]:
    return [f"{prefix}{name}={_value(value)}" for name, value in snapshot.parameters.items()]


def lint_command(snapshot: ExecutionSnapshot) -> list[str]:
    return [
        "verilator",
        "--lint-only",
        "--Wall",
        "--Wno-fatal",
        "--language",
        "1800-2012",
        "--top-module",
        snapshot.top_module,
        *[f"-I/workspace/{path}" for path in snapshot.include_dirs],
        *_parameter_args(snapshot, "-G"),
        *[f"/workspace/{item.path}" for item in snapshot.rtl_manifest],
    ]


def compile_command(snapshot: ExecutionSnapshot) -> list[str]:
    return [
        "iverilog",
        "-g2012",
        "-Wall",
        "-o",
        "/output/simulation.vvp",
        "-s",
        snapshot.testbench_top or "",
        *[f"-I/workspace/{path}" for path in snapshot.include_dirs],
        *[f"-P{snapshot.top_module}.{name}={_value(value)}" for name, value in snapshot.parameters.items()],
        *[f"/workspace/{item.path}" for item in snapshot.rtl_manifest],
        *[f"/workspace/{item.path}" for item in snapshot.testbench_manifest],
    ]


def simulation_command() -> list[str]:
    return ["vvp", "/output/simulation.vvp"]


def _ys_quote(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_./$+-]+", value):
        raise ValueError("unsafe generated Yosys path")
    # The generated script accepts only the narrow path alphabet above, so no
    # shell/Yosys quoting is needed. Quoting here would become part of the
    # filename for Yosys' script parser.
    return value


def build_yosys_script(snapshot: ExecutionSnapshot) -> str:
    lines: list[str] = []
    for item in snapshot.rtl_manifest:
        command = "read_verilog -sv" if Path(item.path).suffix.lower() in {".sv", ".svh"} else "read_verilog"
        lines.append(f"{command} {_ys_quote('/workspace/' + item.path)}")
    for name, value in snapshot.parameters.items():
        lines.append(f"chparam -set {validate_identifier(name)} {_value(value)} {validate_identifier(snapshot.top_module)}")
    lines.extend([
        f"hierarchy -check -top {validate_identifier(snapshot.top_module)}",
        "proc",
        "opt",
        "memory",
        "opt",
        "check",
        "stat",
        "write_verilog -noattr -noexpr -simple-lhs /output/synthesized.v",
        "write_json /output/netlist.json",
    ])
    return "\n".join(lines) + "\n"


def parse_diagnostics(text: str, tool: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        severity: str | None = None
        if re.search(r"%Error|\bERROR\b|\berror:\b|\bfatal\b", line, re.IGNORECASE):
            severity = "error"
        elif re.search(r"%Warning|\bWARNING\b|\bWarning:\b|\bwarning:\b", line, re.IGNORECASE):
            severity = "warning"
        if severity is None and re.search(r"syntax error|elaborat|assertion failed", line, re.IGNORECASE):
            severity = "error"
        if severity is None:
            continue
        match = re.search(r"(?P<file>[^\s:]+):(\d+)(?::(\d+))?", line)
        diagnostics.append(
            Diagnostic(
                severity=severity,
                message=line[:1000],
                raw=raw[:2000],
                file=match.group("file") if match else None,
                line=int(match.group(2)) if match else None,
                column=int(match.group(3)) if match and match.group(3) else None,
            )
        )
    return diagnostics


def parse_metrics(text: str) -> dict[str, int | str]:
    metrics: dict[str, int | str] = {}
    patterns = {
        "cells": r"Number of cells:\s+(\d+)",
        "wires": r"Number of wires:\s+(\d+)",
        "wire_bits": r"Number of wire bits:\s+(\d+)",
        "memories": r"Number of memories:\s+(\d+)",
        "processes": r"Number of processes:\s+(\d+)",
    }
    for name, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            metrics[name] = int(match.group(1))
    return metrics


CHECK_RE = re.compile(r"CIRCUITPILOT_CHECK\s+id=([A-Za-z0-9_.:-]+)\s+status=(passed|failed)", re.IGNORECASE)


def parse_testbench(text: str) -> TestbenchReport:
    checks = CHECK_RE.findall(text)
    check_ids = [item[0] for item in checks]
    failed = sum(1 for _, status in checks if status.lower() == "failed")
    passed = len(checks) - failed
    pass_marker = "CIRCUITPILOT_TESTBENCH_PASS" in text
    fail_marker = "CIRCUITPILOT_TESTBENCH_FAIL" in text
    status: str = "unknown"
    marker = None
    limitation = "The process completed without a valid CircuitPilot testbench completion report."
    if fail_marker or failed:
        status = "failed"
        marker = "CIRCUITPILOT_TESTBENCH_FAIL" if fail_marker else None
        limitation = None
    elif pass_marker and checks and passed == len(checks):
        status = "passed"
        marker = "CIRCUITPILOT_TESTBENCH_PASS"
        limitation = "Checks and completion are reported by the testbench; this is not independent proof of hardware correctness."
    return TestbenchReport(
        status=status,
        checks_total=len(checks),
        checks_passed=passed,
        checks_failed=failed,
        check_ids=check_ids,
        completion_marker=marker,
        limitation=limitation,
    )


def _stage_status(process: ProcessResult, report: TestbenchReport | None = None) -> str:
    if process.termination_reason == "timeout":
        return "timed_out"
    if process.termination_reason == "cancelled":
        return "cancelled"
    if process.exit_code != 0:
        return "failed"
    if report and report.status == "failed":
        return "failed"
    return "completed"


def _tool_name(stage: str) -> str:
    return {"lint": "verilator", "compilation": "iverilog", "simulation": "vvp", "synthesis": "yosys"}.get(stage, stage)


def _version_for(snapshot: ExecutionSnapshot, stage: str) -> str | None:
    tool = _tool_name(stage)
    versions = snapshot.toolchain.get("tool_versions", {}) if isinstance(snapshot.toolchain, dict) else {}
    return versions.get(tool)


class ExecutionService:
    def __init__(self, app):
        self.app = app
        self.settings: Settings = app.state.settings
        self.workspace: WorkspaceService = app.state.workspace
        self.runtime = app.state.toolchain_runtime

    def _session(self) -> Session:
        return self.app.state.session_factory()

    def add_event(self, session: Session, run_id: str, event_type: str, summary: str, stage_name: str | None = None, metadata: dict | None = None) -> None:
        session.add(RunEvent(run_id=run_id, stage_name=stage_name, event_type=event_type, summary=summary, metadata_json=metadata))

    def add_project_event(self, session: Session, project_id: str, event_type: str, summary: str, metadata: dict | None = None) -> None:
        session.add(ProjectEvent(project_id=project_id, event_type=event_type, summary=summary, metadata_json=metadata))

    async def _run_stage(self, session: Session, run: Run, snapshot: ExecutionSnapshot, snapshot_dir: Path, output_dir: Path, name: str, command: list[str]) -> RunStage:
        stage = RunStage(run_id=run.id, name=name, status="running", tool=_tool_name(name), tool_version=_version_for(snapshot, name), started_at=utc_now())
        session.add(stage)
        self.add_event(session, run.id, "stage.started", f"Running {name}.", name)
        session.commit()
        result: ProcessResult
        try:
            result = await self.runtime.run(run.id, snapshot_dir, output_dir, command, self.settings.toolchain_timeout_seconds)
        except asyncio.CancelledError:
            stage.status = "cancelled"
            stage.termination_reason = "cancelled"
            stage.completed_at = utc_now()
            session.commit()
            raise
        combined = (result.stdout + ("\n" if result.stdout and result.stderr else "") + result.stderr).strip()
        if len(combined.encode("utf-8")) > self.settings.toolchain_max_log_bytes:
            combined = combined.encode("utf-8")[: self.settings.toolchain_max_log_bytes].decode("utf-8", errors="ignore")
            result.log_truncated = True
            combined += "\n[log truncated by CircuitPilot]"
        log_path = output_dir / f"{name}.log"
        log_path.write_text(combined, encoding="utf-8")
        report = parse_testbench(combined) if name == "simulation" else None
        diagnostics = parse_diagnostics(combined, _tool_name(name))
        stage.status = _stage_status(result, report)
        stage.tool = _tool_name(name)
        stage.exit_code = result.exit_code
        stage.termination_reason = result.termination_reason
        stage.diagnostics_json = [item.model_dump() for item in diagnostics]
        stage.testbench_json = report.model_dump() if report else None
        stage.metrics_json = parse_metrics(combined) if name == "synthesis" else {}
        stage.log_truncated = result.log_truncated
        stage.completed_at = utc_now()
        stage.duration_ms = result.duration_ms
        session.flush()
        self._collect_artifacts(session, run, output_dir, name, stage, log_path)
        self.add_event(session, run.id, "stage.completed", f"{name.capitalize()} {stage.status}.", name, {"exit_code": result.exit_code})
        session.commit()
        return stage

    def _collect_artifacts(self, session: Session, run: Run, output_dir: Path, stage_name: str, stage: RunStage, log_path: Path) -> None:
        artifact_root = self.settings.execution_artifact_root / run.id
        artifact_root.mkdir(parents=True, exist_ok=True)
        existing_artifacts = session.scalars(select(ArtifactReference).where(ArtifactReference.run_id == run.id)).all()
        existing_paths = {item.relative_path for item in existing_artifacts}
        total = sum((item.size_bytes or 0) for item in existing_artifacts)
        artifact_ids: list[str] = []
        for source in sorted(output_dir.rglob("*")):
            if source.is_symlink():
                raise ApiError(500, "unsafe_artifact", "The tool produced a symlink artifact, which was rejected.")
            if not source.is_file():
                continue
            relative = source.relative_to(output_dir)
            stored_relative = (Path(run.id) / relative).as_posix()
            if stored_relative in existing_paths:
                continue
            if len(relative.parts) > 8:
                raise ApiError(500, "unsafe_artifact", "The tool produced an artifact with an invalid path.")
            size = source.stat().st_size
            if size > self.settings.toolchain_max_artifact_bytes:
                raise ApiError(500, "artifact_too_large", "A tool artifact exceeded the configured size limit.")
            total += size
            if total > self.settings.toolchain_max_total_artifact_bytes:
                raise ApiError(500, "artifacts_too_large", "The tool artifacts exceeded the configured total size limit.")
            destination = artifact_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            data = destination.read_bytes()
            artifact = ArtifactReference(
                run_id=run.id,
                kind=self._artifact_kind(source),
                relative_path=destination.relative_to(self.settings.execution_artifact_root).as_posix(),
                name=relative.name,
                size_bytes=size,
                sha256=sha256_bytes(data),
                content_type=mimetypes.guess_type(source.name)[0] or "application/octet-stream",
                stage=stage_name,
            )
            session.add(artifact)
            session.flush()
            existing_paths.add(stored_relative)
            artifact_ids.append(artifact.id)
            if source == log_path:
                stage.log_artifact_id = artifact.id
        stage.artifact_ids_json = artifact_ids

    @staticmethod
    def _artifact_kind(path: Path) -> str:
        return {".vcd": "waveform", ".vvp": "simulation_binary", ".json": "netlist", ".v": "netlist", ".log": "log"}.get(path.suffix.lower(), "report")

    async def execute(self, run_id: str) -> None:
        session = self._session()
        snapshot_dir: Path | None = None
        try:
            run = session.get(Run, run_id)
            if run is None or run.status not in ACTIVE_EXECUTION_STATES:
                return
            run.status = "running"
            run.started_at = utc_now()
            session.commit()
            snapshot = ExecutionSnapshot.model_validate(run.snapshot_json)
            snapshot_dir = self.settings.execution_snapshot_root / run.id
            output_dir = self.settings.execution_output_root / run.id
            self.add_event(session, run.id, "run.started", "Execution started.")
            session.commit()
            stage_results: list[RunStage] = []

            def active() -> bool:
                session.expire_all()
                current = session.get(Run, run_id)
                return bool(current and not current.cancel_requested and current.status in {"running", "queued"})

            if run.stage in {"lint", "checks"} and active():
                stage_results.append(await self._run_stage(session, run, snapshot, snapshot_dir, output_dir, "lint", lint_command(snapshot)))
            lint_failed = any(item.status in {"failed", "timed_out", "cancelled"} for item in stage_results if item.name == "lint")
            if run.stage == "checks" and lint_failed:
                for skipped in ("compilation", "simulation", "synthesis"):
                    stage_results.append(self._skipped_stage(session, run, skipped, "A blocking lint result prevented this stage."))
            if run.stage in {"simulation", "checks"} and active() and not lint_failed:
                compilation = await self._run_stage(session, run, snapshot, snapshot_dir, output_dir, "compilation", compile_command(snapshot))
                stage_results.append(compilation)
                if compilation.status == "completed" and active():
                    stage_results.append(await self._run_stage(session, run, snapshot, snapshot_dir, output_dir, "simulation", simulation_command()))
                elif run.stage == "checks":
                    stage_results.append(self._skipped_stage(session, run, "simulation", "Compilation did not complete."))
            simulation_failed = any(item.status in {"failed", "timed_out", "cancelled"} for item in stage_results if item.name in {"compilation", "simulation"})
            if run.stage in {"synthesis", "checks"} and active() and not (lint_failed or simulation_failed):
                stage_results.append(await self._run_stage(session, run, snapshot, snapshot_dir, output_dir, "synthesis", ["yosys", "-s", "/workspace/__circuitpilot/synthesis.ys"]))
            elif run.stage == "checks" and (lint_failed or simulation_failed):
                if not any(item.name == "synthesis" for item in stage_results):
                    stage_results.append(self._skipped_stage(session, run, "synthesis", "An earlier stage did not complete."))
            session.expire_all()
            run = session.get(Run, run_id)
            if run is None:
                return
            if run.cancel_requested:
                run.status = "cancelled"
            elif any(stage.status == "timed_out" for stage in stage_results):
                run.status = "timed_out"
            elif any(stage.status in {"failed", "cancelled"} for stage in stage_results):
                run.status = "failed"
            else:
                run.status = "completed"
            run.completed_at = utc_now()
            self.add_event(session, run.id, "run.completed", f"Execution {run.status}.")
            self.add_project_event(session, run.project_id, "execution.completed", f"Execution {run.status}.", {"run_id": run.id})
            session.commit()
        except asyncio.CancelledError:
            session.rollback()
            run = session.get(Run, run_id)
            if run and run.status not in TERMINAL_EXECUTION_STATES:
                run.status = "cancelled"
                run.completed_at = utc_now()
                self.add_event(session, run.id, "run.cancelled", "Execution cancelled.")
                session.commit()
            raise
        except Exception as exc:
            session.rollback()
            run = session.get(Run, run_id)
            if run and run.status not in TERMINAL_EXECUTION_STATES:
                run.status = "failed"
                run.error_code = getattr(exc, "detail", {}).get("code", "execution_failed") if isinstance(getattr(exc, "detail", None), dict) else "execution_failed"
                run.error_message = f"The engineering tool execution failed ({type(exc).__name__}). Inspect the recorded stage logs for details."
                run.completed_at = utc_now()
                self.add_event(session, run.id, "run.failed", run.error_message)
                session.commit()
        finally:
            if 'output_dir' in locals():
                shutil.rmtree(output_dir, ignore_errors=True)
            session.close()

    def _skipped_stage(self, session: Session, run: Run, name: str, reason: str) -> RunStage:
        stage = RunStage(
            run_id=run.id,
            name=name,
            status="skipped",
            tool=_tool_name(name),
            termination_reason="dependency_failed",
            diagnostics_json=[],
            metrics_json={},
            artifact_ids_json=[],
        )
        session.add(stage)
        self.add_event(session, run.id, "stage.skipped", f"{name.capitalize()} skipped: {reason}", name)
        session.commit()
        return stage


class ExecutionManager:
    def __init__(self, app):
        self.app = app
        self.tasks: dict[str, asyncio.Task] = {}

    async def start(self, run_id: str) -> None:
        task = asyncio.create_task(ExecutionService(self.app).execute(run_id))
        self.tasks[run_id] = task

        def cleanup(_task):
            self.tasks.pop(run_id, None)

        task.add_done_callback(cleanup)

    async def cancel(self, run_id: str) -> bool:
        session = self.app.state.session_factory()
        try:
            run = session.get(Run, run_id)
            if run is None or run.status not in ACTIVE_EXECUTION_STATES:
                return False
            run.cancel_requested = True
            session.commit()
        finally:
            session.close()
        task = self.tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        return True


def recover_interrupted_execution_runs(session_factory, runtime: ContainerRuntime | None = None) -> None:
    session = session_factory()
    try:
        runs = session.scalars(select(Run).where(Run.kind == "engineering", Run.status.in_(ACTIVE_EXECUTION_STATES))).all()
        run_ids = [run.id for run in runs]
        for run in runs:
            run.status = "interrupted"
            run.error_code = "backend_restarted"
            run.error_message = "The backend restarted before this execution completed. No result is inferred."
            run.completed_at = utc_now()
            session.add(RunEvent(run_id=run.id, event_type="run.interrupted", summary="Execution marked interrupted after backend restart."))
        session.commit()
        if runtime and run_ids:
            runtime.reconcile(run_ids)
    finally:
        session.close()


def execution_result(session: Session, run: Run) -> ExecutionRunResult:
    stages: list[StageResult] = []
    for stage in run.stages:
        stages.append(StageResult(
            stage=stage.name,
            status=stage.status,
            tool=stage.tool or stage.name,
            tool_version=stage.tool_version,
            started_at=stage.started_at,
            completed_at=stage.completed_at,
            duration_ms=stage.duration_ms,
            exit_code=stage.exit_code,
            termination_reason=stage.termination_reason,
            diagnostics=[Diagnostic.model_validate(item) for item in (stage.diagnostics_json or [])],
            testbench_report=TestbenchReport.model_validate(stage.testbench_json) if stage.testbench_json else None,
            metrics=stage.metrics_json or {},
            artifact_ids=stage.artifact_ids_json or [],
            log_artifact_id=stage.log_artifact_id,
            log_truncated=stage.log_truncated,
        ))
    return ExecutionRunResult(
        id=run.id,
        project_id=run.project_id,
        status=run.status,
        stage=run.stage,
        snapshot_id=run.snapshot_id,
        specification_revision=run.specification_revision,
        generation_proposal_id=run.generation_proposal_id,
        error_code=run.error_code,
        error_message=run.error_message,
        cancel_requested=run.cancel_requested,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        stages=stages,
        events=[
            {"id": item.id, "stage_name": item.stage_name, "event_type": item.event_type, "summary": item.summary, "metadata": item.metadata_json, "created_at": item.created_at}
            for item in run.events
        ],
        artifacts=[
            {"id": item.id, "kind": item.kind, "name": item.name, "size_bytes": item.size_bytes, "sha256": item.sha256, "content_type": item.content_type, "stage": item.stage, "created_at": item.created_at}
            for item in run.artifacts
        ],
    )
