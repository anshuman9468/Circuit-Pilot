from __future__ import annotations

import asyncio
import os

import pytest

from app.config import Settings
from app.services.toolchain import ContainerRuntime


pytestmark = pytest.mark.integration


def test_real_container_toolchain_smoke(tmp_path):
    if os.environ.get("CIRCUITPILOT_RUN_TOOLCHAIN_TESTS") != "1":
        pytest.skip("Set CIRCUITPILOT_RUN_TOOLCHAIN_TESTS=1 for explicit container integration tests.")
    settings = Settings(
        execution_snapshot_root=tmp_path / "snapshots",
        execution_output_root=tmp_path / "output",
        execution_artifact_root=tmp_path / "artifacts",
    )
    runtime = ContainerRuntime(settings)
    status = runtime.status()
    if not (status.runtime_available and status.image_available and len(status.tool_versions) == 4):
        pytest.skip(status.message)
    snapshot_dir = settings.execution_snapshot_root / "integration"
    output_dir = settings.execution_output_root / "integration"
    snapshot_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (snapshot_dir / "top.sv").write_text("module top(input logic a, output logic y); assign y = a; endmodule\n", encoding="utf-8")
    (snapshot_dir / "top_tb.sv").write_text(
        'module top_tb; logic a; logic y; top dut(.a(a), .y(y)); initial begin a=0; #1; if (y !== 0) $fatal; $display("CIRCUITPILOT_CHECK id=smoke status=passed"); $display("CIRCUITPILOT_TESTBENCH_PASS"); $finish; end endmodule\n',
        encoding="utf-8",
    )
    (snapshot_dir / "synthesis.ys").write_text(
        "read_verilog -sv /workspace/top.sv\nhierarchy -check -top top\nproc; opt; check; stat\nwrite_verilog /output/synthesized.v\nwrite_json /output/netlist.json\n",
        encoding="utf-8",
    )
    lint = asyncio.run(runtime.run("integration-lint", snapshot_dir, output_dir, ["verilator", "--lint-only", "--top-module", "top", "/workspace/top.sv"], 30))
    assert lint.exit_code == 0, lint.stderr
    compile_result = asyncio.run(runtime.run("integration-compile", snapshot_dir, output_dir, ["iverilog", "-g2012", "-o", "/output/sim.vvp", "-s", "top_tb", "/workspace/top.sv", "/workspace/top_tb.sv"], 30))
    assert compile_result.exit_code == 0, compile_result.stderr
    simulation = asyncio.run(runtime.run("integration-simulation", snapshot_dir, output_dir, ["vvp", "/output/sim.vvp"], 30))
    assert simulation.exit_code == 0 and "CIRCUITPILOT_TESTBENCH_PASS" in simulation.stdout
    synthesis = asyncio.run(runtime.run("integration-synthesis", snapshot_dir, output_dir, ["yosys", "-s", "/workspace/synthesis.ys"], 30))
    assert synthesis.exit_code == 0, synthesis.stderr
    assert (output_dir / "synthesized.v").exists()
    assert (output_dir / "netlist.json").exists()
