from __future__ import annotations

import asyncio
import time
from pathlib import Path

from app.config import Settings
from app.execution import ToolchainStatus
from app.main import create_app
from app.services.toolchain import ProcessResult
from app.services.execution_service import parse_testbench
from fastapi.testclient import TestClient

from conftest import create_project


class FakeRuntime:
    def __init__(self, *, delay: float = 0):
        self.delay = delay
        self.commands: list[list[str]] = []
        self.cancelled = False

    def status(self) -> ToolchainStatus:
        return ToolchainStatus(
            runtime_requested="fake",
            runtime="fake",
            runtime_available=True,
            runtime_version="fake-runtime 1",
            image="test/toolchain:fixture",
            image_available=True,
            tool_versions={
                "verilator": "Verilator 5.test",
                "iverilog": "Icarus Verilog version 12.test",
                "vvp": "vvp 12.test",
                "yosys": "Yosys 0.test",
            },
            checked_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            message="Fake runtime for tests.",
        )

    async def run(self, run_id, snapshot_dir: Path, output_dir: Path, command, timeout_seconds):
        self.commands.append(list(command))
        if self.delay:
            await asyncio.sleep(self.delay)
        tool = command[0]
        if tool == "iverilog":
            (output_dir / "simulation.vvp").write_text("test binary", encoding="utf-8")
            return ProcessResult(0, "", "", 2)
        if tool == "vvp":
            (output_dir / "wave.vcd").write_text("$date\n$end", encoding="utf-8")
            return ProcessResult(0, "CIRCUITPILOT_CHECK id=smoke status=passed\nCIRCUITPILOT_TESTBENCH_PASS\n", "", 3)
        if tool == "yosys":
            (output_dir / "synthesized.v").write_text("module top; endmodule\n", encoding="utf-8")
            (output_dir / "netlist.json").write_text("{}", encoding="utf-8")
            return ProcessResult(0, "Number of cells: 2\nNumber of wires: 3\nNumber of wire bits: 4\n", "", 4)
        return ProcessResult(0, "", "", 1)

    def reconcile(self, run_ids):
        return None


def app_with_runtime(tmp_path, runtime=None):
    runtime = runtime or FakeRuntime()
    settings = Settings(
        database_path=tmp_path / "circuitpilot.db",
        workspace_root=tmp_path / "workspaces",
        execution_snapshot_root=tmp_path / "snapshots",
        execution_output_root=tmp_path / "output",
        execution_artifact_root=tmp_path / "artifacts",
        allowed_origins=("http://testserver",),
        max_file_size=4096,
        max_request_size=32 * 1024,
        max_requirements_size=4096,
    )
    return create_app(settings, execution_runtime=runtime), runtime


def add_file(client, project_id, path, content):
    response = client.post(f"/api/projects/{project_id}/files", json={"path": path, "content": content})
    assert response.status_code == 201, response.text


def wait_for_run(client, project_id, run_id):
    for _ in range(100):
        result = client.get(f"/api/projects/{project_id}/execution/runs/{run_id}").json()
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(0.01)
    raise AssertionError("run did not finish")


def test_execution_pipeline_persists_snapshot_and_artifacts(tmp_path):
    app, runtime = app_with_runtime(tmp_path)
    with TestClient(app) as client:
        project = create_project(client, name="Execution project")
        add_file(client, project["id"], "rtl/top.sv", "module top(input logic a, output logic y); assign y = a; endmodule\n")
        add_file(client, project["id"], "tb/top_tb.sv", "module top_tb; endmodule\n")
        response = client.post(f"/api/projects/{project['id']}/execution/runs", json={
            "stage": "checks",
            "rtl_paths": ["rtl/top.sv"],
            "testbench_paths": ["tb/top_tb.sv"],
            "top_module": "top",
            "testbench_top": "top_tb",
        })
        assert response.status_code == 202, response.text
        result = wait_for_run(client, project["id"], response.json()["id"])
        assert result["status"] == "completed"
        assert [stage["stage"] for stage in result["stages"]] == ["lint", "compilation", "simulation", "synthesis"]
        simulation = next(stage for stage in result["stages"] if stage["stage"] == "simulation")
        assert simulation["testbench_report"]["checks_passed"] == 1
        assert any(artifact["kind"] == "waveform" for artifact in result["artifacts"])
        assert any(command[0] == "yosys" and all("tb/" not in item for item in command) for command in runtime.commands)
        persisted = client.get(f"/api/projects/{project['id']}/execution/runs").json()
        assert persisted[0]["snapshot_id"] == result["snapshot_id"]

        add_file(client, project["id"], "rtl/edited.txt", "not HDL")
        after_edit = client.get(f"/api/projects/{project['id']}/execution/runs/{result['id']}").json()
        assert after_edit["snapshot_id"] == result["snapshot_id"]


def test_execution_rejects_cross_project_and_traversal(tmp_path):
    app, _ = app_with_runtime(tmp_path)
    with TestClient(app) as client:
        first = create_project(client, name="One")
        second = create_project(client, name="Two")
        add_file(client, first["id"], "rtl/top.sv", "module top; endmodule")
        payload = {"stage": "lint", "rtl_paths": ["../rtl/top.sv"], "top_module": "top"}
        assert client.post(f"/api/projects/{first['id']}/execution/runs", json=payload).status_code == 422
        payload["rtl_paths"] = ["rtl/top.sv"]
        assert client.post(f"/api/projects/{second['id']}/execution/runs", json=payload).status_code == 400


def test_execution_requires_image_and_valid_testbench_contract(tmp_path):
    runtime = FakeRuntime()
    runtime.status = lambda: ToolchainStatus(
        runtime_requested="podman", runtime="podman", runtime_available=True, runtime_version="podman", image="missing", image_available=False,
        checked_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), message="Image unavailable.", setup_instructions=["build it"],
    )
    app, _ = app_with_runtime(tmp_path, runtime)
    with TestClient(app) as client:
        project = create_project(client)
        add_file(client, project["id"], "rtl/top.sv", "module top; endmodule")
        response = client.post(f"/api/projects/{project['id']}/execution/runs", json={"stage": "lint", "rtl_paths": ["rtl/top.sv"], "top_module": "top"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "toolchain_unavailable"
        assert parse_testbench("vvp finished normally").status == "unknown"
