FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --no-install-recommends --yes verilator iverilog yosys ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin circuitpilot \
    && mkdir -p /workspace /output \
    && chown -R 10001:10001 /workspace /output

USER 10001:10001
WORKDIR /workspace

# The runtime mounts project snapshots read-only at /workspace and a bounded
# writable artifact directory at /output.
