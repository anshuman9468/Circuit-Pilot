# Phase 5 verification image record

This is the image built during Phase 5 verification from `toolchain/Dockerfile`:

```text
Image tag: circuitpilot/toolchain:phase5
Podman image ID: 98c59a586bcc1fb6a493b3843697b39e188dd2a061a037eb1e5c9beb4fbbf910
Base: debian:bookworm-slim
Verilator: 5.006 2023-01-22 (Debian 5.006-3)
Icarus Verilog: 11.0 (stable)
Icarus vvp: 11.0 (stable)
Yosys: 0.23 (git sha1 7ce5011c24b)
```

The base-image tag and package repository can change when the image is rebuilt.
For a strict reproducible deployment, record the resulting image ID (or pin a
reviewed base-image digest) and set `TOOLCHAIN_IMAGE` to that reviewed image.
Every application run still records the actual runtime/tool versions it saw.
