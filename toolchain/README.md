# CircuitPilot Phase 5 tool image

This image is an explicit setup prerequisite. CircuitPilot never builds or
pulls it from a health check and never falls back to unrestricted host tools.

Build it locally with either runtime:

```bash
podman build --tag circuitpilot/toolchain:phase5 ./toolchain
# or
docker build --tag circuitpilot/toolchain:phase5 ./toolchain
```

Record the exact image identity and installed versions for a reproducible run:

```bash
podman image inspect --format '{{.Id}}' circuitpilot/toolchain:phase5
podman run --rm --network none circuitpilot/toolchain:phase5 verilator --version
podman run --rm --network none circuitpilot/toolchain:phase5 iverilog -V
podman run --rm --network none circuitpilot/toolchain:phase5 vvp -V
podman run --rm --network none circuitpilot/toolchain:phase5 yosys -V
```

The backend stores these versions and the image identity/status with every
execution. The selected runtime is rootless Podman by default when available;
Docker is supported when configured with `TOOLCHAIN_RUNTIME=docker`.
