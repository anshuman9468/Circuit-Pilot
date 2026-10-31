from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..config import Settings
from ..execution import ToolchainStatus


@dataclass
class ProcessResult:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    termination_reason: str | None = None
    log_truncated: bool = False


class ContainerRuntime:
    """Run the fixed HDL tool commands in a rootless container.

    The service never constructs a shell command from user input and never
    falls back to running Verilator/Icarus/Yosys on the host.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def _candidate_runtimes(self) -> list[str]:
        if self.settings.toolchain_runtime != "auto":
            return [self.settings.toolchain_runtime]
        return ["podman", "docker"]

    def executable(self) -> str | None:
        return next((candidate for candidate in self._candidate_runtimes() if shutil.which(candidate)), None)

    def _run_sync(self, args: Sequence[str], timeout: float = 10) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None

    def status(self) -> ToolchainStatus:
        checked_at = datetime.now(timezone.utc)
        runtime = self.executable()
        setup: list[str] = []
        if runtime is None:
            setup.extend([
                "Install Podman or Docker and make the selected runtime available on PATH.",
                "Build the tool image explicitly with: podman build --tag circuitpilot/toolchain:phase5 ./toolchain",
            ])
            return ToolchainStatus(
                runtime_requested=self.settings.toolchain_runtime,
                runtime=None,
                runtime_available=False,
                runtime_version=None,
                image=self.settings.toolchain_image,
                image_available=False,
                checked_at=checked_at,
                message="No supported container runtime was found. Host execution is intentionally disabled.",
                setup_instructions=setup,
            )

        version_result = self._run_sync([runtime, "--version"])
        runtime_version = (version_result.stdout or version_result.stderr).strip().splitlines()[0] if version_result else None
        image_result = self._run_sync([runtime, "image", "inspect", self.settings.toolchain_image])
        image_available = bool(image_result and image_result.returncode == 0)
        if not image_available:
            setup.extend([
                f"Build the image explicitly: {runtime} build --tag {self.settings.toolchain_image} ./toolchain",
                f"Or configure TOOLCHAIN_IMAGE to an existing trusted image containing Verilator, Icarus Verilog, vvp, and Yosys.",
            ])
            return ToolchainStatus(
                runtime_requested=self.settings.toolchain_runtime,
                runtime=runtime,
                runtime_available=True,
                runtime_version=runtime_version,
                image=self.settings.toolchain_image,
                image_available=False,
                checked_at=checked_at,
                message="Container runtime is available, but the configured tool image is not built or available locally.",
                setup_instructions=setup,
            )

        tool_versions: dict[str, str] = {}
        for tool, args in (("verilator", ["verilator", "--version"]), ("iverilog", ["iverilog", "-V"]), ("vvp", ["vvp", "-V"]), ("yosys", ["yosys", "-V"])):
            result = self._run_sync(self._container_args("status", Path("/tmp"), Path("/tmp"), args), timeout=20)
            if result and result.returncode == 0:
                version_text = (result.stdout or result.stderr).strip().splitlines()
                if version_text:
                    tool_versions[tool] = version_text[0][:160]
        missing = sorted({"verilator", "iverilog", "vvp", "yosys"} - set(tool_versions))
        if missing:
            setup.append("Rebuild the tool image so it contains all required tools: Verilator, Icarus Verilog/vvp, and Yosys.")
        return ToolchainStatus(
            runtime_requested=self.settings.toolchain_runtime,
            runtime=runtime,
            runtime_available=True,
            runtime_version=runtime_version,
            image=self.settings.toolchain_image,
            image_available=True,
            tool_versions=tool_versions,
            checked_at=checked_at,
            message="Toolchain image is ready." if not missing else f"Toolchain image is missing: {', '.join(missing)}.",
            setup_instructions=setup,
        )

    def _container_args(self, run_id: str, snapshot_dir: Path, output_dir: Path, command: Sequence[str]) -> list[str]:
        runtime = self.executable()
        if runtime is None:
            raise RuntimeError("container runtime unavailable")
        name = f"circuitpilot-{run_id[:18]}"
        args = [
            runtime,
            "run",
            "--rm",
            "--name",
            name,
            "--label",
            "circuitpilot.managed=true",
            "--label",
            f"circuitpilot.run_id={run_id}",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.settings.toolchain_pids_limit),
            "--memory",
            self.settings.toolchain_memory,
            "--cpus",
            self.settings.toolchain_cpus,
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--volume",
            f"{snapshot_dir.resolve()}:/workspace:ro",
            "--volume",
            f"{output_dir.resolve()}:/output:rw",
            "--user",
            "10001:10001",
            self.settings.toolchain_image,
            *command,
        ]
        # Fedora/SELinux hosts require an explicit label opt-out for the
        # backend-created read-only snapshot bind mount. This does not weaken
        # the process/container controls above and is ignored for Docker.
        if runtime == "podman":
            security_index = args.index("--security-opt")
            args[security_index + 2:security_index + 2] = ["--security-opt", "label=disable"]
        return args

    async def _bounded_reader(self, stream, limit: int) -> tuple[str, bool]:
        chunks: list[bytes] = []
        total = 0
        truncated = False
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            if total < limit:
                remaining = limit - total
                chunks.append(chunk[:remaining])
                total += min(len(chunk), remaining)
            if total >= limit and len(chunk) + total > limit:
                truncated = True
        return b"".join(chunks).decode("utf-8", errors="replace"), truncated

    async def run(
        self,
        run_id: str,
        snapshot_dir: Path,
        output_dir: Path,
        command: Sequence[str],
        timeout_seconds: float | None = None,
    ) -> ProcessResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_dir.chmod(0o777)
        args = self._container_args(run_id, snapshot_dir, output_dir, command)
        start = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_task = asyncio.create_task(self._bounded_reader(process.stdout, self.settings.toolchain_max_log_bytes))
        stderr_task = asyncio.create_task(self._bounded_reader(process.stderr, self.settings.toolchain_max_log_bytes))
        termination_reason: str | None = None
        try:
            timeout = timeout_seconds or self.settings.toolchain_timeout_seconds
            try:
                exit_code = await asyncio.wait_for(process.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                termination_reason = "timeout"
                process.kill()
                await process.wait()
                exit_code = None
            except asyncio.CancelledError:
                termination_reason = "cancelled"
                process.kill()
                await process.wait()
                raise
            stdout, stdout_truncated = await stdout_task
            stderr, stderr_truncated = await stderr_task
            return ProcessResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=int((time.monotonic() - start) * 1000),
                termination_reason=termination_reason,
                log_truncated=stdout_truncated or stderr_truncated,
            )
        finally:
            if not stdout_task.done():
                stdout_task.cancel()
            if not stderr_task.done():
                stderr_task.cancel()
            self._remove_container(run_id)

    def _remove_container(self, run_id: str) -> None:
        runtime = self.executable()
        if runtime is None:
            return
        name = f"circuitpilot-{run_id[:18]}"
        try:
            subprocess.run([runtime, "rm", "-f", name], capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def reconcile(self, run_ids: list[str]) -> None:
        runtime = self.executable()
        if runtime is None:
            return
        for run_id in run_ids:
            name = f"circuitpilot-{run_id[:18]}"
            try:
                subprocess.run([runtime, "rm", "-f", name], capture_output=True, text=True, timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                continue
