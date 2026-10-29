# CircuitPilot

CircuitPilot is a local, single-user digital hardware engineering workspace. Phase 3 adds an optional feature-gated Meta specification-planning workflow; Phase 4 adds a feature-gated, staged RTL and self-checking testbench proposal workflow on top of the Phase 1–3 foundation.

The assistant can discuss a project and use three read-only, project-bound tools to inspect saved requirements and text files. The Specification view lets a user turn a natural-language request into a versioned structured specification, module plan, and planned verification objectives using deterministic validation. A ready revision can then be sent through the explicit Phase 4 generation path. Generation produces a validated proposal in a separate persistent record; it does not compile, simulate, synthesize, or claim verification results.

## Prerequisites

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/) 0.4 or newer
- Node.js 20 or newer and npm

## Install

From the repository root:

```bash
cd backend
uv sync

cd ../frontend
npm install
```

`backend/uv.lock` and `frontend/package-lock.json` are checked in.

## Configure

Copy the example configuration and edit the backend values when Meta access is available:

```bash
cp .env.example .env
```

The application starts without a model key. Keep `MODEL_API_KEY` in this backend-only `.env` file or in the backend process environment. Never put it in a `NEXT_PUBLIC_*` variable, frontend source, a request body, or this chat. `.env` files are ignored by Git.

| Variable | Purpose |
| --- | --- |
| `BACKEND_HOST` / `BACKEND_PORT` | FastAPI bind address and port |
| `NEXT_PUBLIC_API_URL` | Browser API base URL, normally `http://127.0.0.1:8000/api`; this is not a credential |
| `DATABASE_PATH` | SQLite database file; relative paths resolve from the repository root |
| `WORKSPACE_ROOT` | Project workspace root; absolute paths are recommended |
| `ALLOWED_ORIGINS` | Comma-separated browser origins allowed by FastAPI CORS |
| `MODEL_API_KEY` | Backend-only Meta API key; optional until conversations are needed |
| `META_BASE_URL` | Defaults to `https://api.meta.ai/v1` |
| `META_MODEL` | Defaults to `muse-spark-1.3-contributor` |
| `MODEL_PRIMARY_ENABLED` | Enables the Meta primary provider; set `false` when intentionally routing local requests directly to the configured fallback |
| `MODEL_FALLBACK_ENABLED` | Enables fallback to the configured OpenAI-compatible fallback provider; defaults to `false` |
| `MODEL_FALLBACK_ON_TRANSIENT_ERRORS` | Explicitly permits fallback after Meta timeouts or connection failures; defaults to `false` because a timed-out request may already have been accepted |
| `MODEL_FALLBACK_API_KEY` | Backend-only fallback key; never expose it to the browser or commit it |
| `MODEL_FALLBACK_BASE_URL` | Defaults to `https://openrouter.ai/api/v1` |
| `MODEL_FALLBACK_MODEL` | Defaults to OpenRouter's `openrouter/free` router, which selects a currently available free model |
| `MODEL_TIMEOUT_SECONDS` | Provider request timeout |
| `MAX_MODEL_ROUNDS` | Maximum model turns in one bounded run |
| `MAX_TOOL_CALLS` | Maximum read-only tool calls across one run |
| `MAX_COMPLETION_TOKENS` | Bounded `max_completion_tokens` sent to Chat Completions; `8192` is the documented default so structured planner responses are not cut off by reasoning-heavy free models |
| `MAX_CONTEXT_CHARS` | Maximum safely reconstructed provider context |
| `MAX_TOOL_RESULT_CHARS` | Maximum returned requirements/file tool content |
| `MAX_ACTIVE_RUNS` | Maximum simultaneous active runs for this backend process |
| `MAX_USER_MESSAGE_CHARS` | Maximum submitted conversation message size |
| `SPECIFICATION_PLANNER_ENABLED` | Explicit Meta structured-specification planning flag; defaults to `false` |
| `MAX_PLANNER_CORRECTIONS` | Maximum bounded correction attempt after malformed planner output |
| `GENERATION_ENABLED` | Explicit Phase 4 live-generation feature flag; defaults to `false` |
| `MAX_GENERATION_OUTPUT_BYTES` | Total generated proposal content limit |
| `MAX_GENERATION_CORRECTIONS` | Maximum bounded contract-correction attempt after malformed output |

The Phase 2 provider contract follows Meta's current documentation:

- [Meta Chat Completions quickstart](https://dev.meta.ai/docs/cookbook/quickstart-chat-completions)
- [Meta Chat Completions protocol](https://dev.meta.ai/docs/protocols/chat-completions)
- [Meta tool calling](https://dev.meta.ai/docs/tool-calling)

The implementation uses the OpenAI Python SDK's asynchronous client, `https://api.meta.ai/v1`, `muse-spark-1.3`, function tools, `tool_choice: auto`, and the documented Chat Completions parameter `max_completion_tokens`. It deliberately disables SDK retries and does not add application retries, avoiding duplicate billable requests.

### Optional OpenRouter fallback

The backend keeps Meta as the primary provider. If `MODEL_FALLBACK_ENABLED=true` and `MODEL_FALLBACK_API_KEY` is configured, it uses the OpenRouter OpenAI-compatible endpoint when Meta is unavailable because the key is missing, authentication fails, access is restricted, an empty response is returned, or the configured request is rejected. It does not replay timeouts or connection failures unless `MODEL_FALLBACK_ON_TRANSIENT_ERRORS=true`, because the original request may already have been accepted. OpenRouter's free Meta Llama endpoint is rate-limited; free access is not a guarantee of tool-call quality or hardware-design correctness.

For a local fallback, add these values to the ignored root `.env` file and restart FastAPI:

```bash
MODEL_FALLBACK_ENABLED=true
MODEL_PRIMARY_ENABLED=false
MODEL_FALLBACK_ON_TRANSIENT_ERRORS=true
MODEL_FALLBACK_API_KEY=your_openrouter_key
MODEL_FALLBACK_BASE_URL=https://openrouter.ai/api/v1
MODEL_FALLBACK_MODEL=openrouter/free
```

OpenRouter documents the free router and its feature filtering at [OpenRouter free models](https://openrouter.ai/openrouter/free), its Chat API's [JSON response format](https://openrouter.ai/docs/client-sdks/python/api-reference/chat), and the provider-specific [reasoning control](https://openrouter.ai/docs/client-sdks/python/api-reference/chat). CircuitPilot requests JSON-object mode for the structured planner when the OpenRouter fallback is active, disables reasoning so the bounded output budget is available for the typed answer, and still validates the result with Pydantic. The router can select different free models over time, so output quality and latency can vary; JSON mode improves contract compliance but does not prove that the proposed design captures user intent. The provider abstraction remains backend-only; keys never appear in API responses, conversation records, or frontend variables. Rotate any key that has been pasted into chat or committed accidentally.

## Run locally

Start FastAPI in one terminal:

```bash
cd backend
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Start Next.js in another:

```bash
cd frontend
npm run dev
```

Open <http://localhost:3000>. The health check is <http://localhost:8000/api/health>.

The model status is visible in the Assistant tab. Use **Test connection** for an explicit provider request; startup, health checks, and page loads never make a billable test call. Then send a message about the current project. Runs are submitted asynchronously, polled by ID, persisted, and can be cancelled or explicitly retried after a recoverable failure. Cancellation stops local scheduling and cancels the in-flight SDK task where supported, but cannot guarantee that a provider request already received by Meta has stopped processing or billing.

If no key is configured, the UI shows **Not configured**. With a key present but no test yet it shows **Configured · not tested**. A successful explicit test or conversation changes the process status to **Connected**. Provider token counts are displayed only when Meta reports them; they are not estimates and no cost is calculated.

Phase 3 works offline by default and does not need a model key. Open a project and select **Specification**. Work through Overview, Parameters and ports, Clocks and resets, Functional behavior, Module-level design plan, Assumptions and open questions, and Verification plan. Each section has explicit editing controls and the bottom **Save draft** action creates a new immutable snapshot revision. **Validate** runs only deterministic structural checks. Open clarifications identify missing or contradictory fields and can be answered directly when a guided answer is available.

Use **Mark ready** only after saving a draft and resolving all blocking findings. A ready revision is an explicit user review state, not proof that the hardware is correct. Editing a ready revision creates a new draft while preserving the earlier ready revision. The revision selector reopens prior snapshots, and **JSON preview** / **Export** exposes the versioned schema for inspection.

The specification schema is versioned as `1.0` and includes the original request, design category, parameters, ports, clocks, reset intent/signals, requirements, timing and boundaries, assumptions, open questions, module connections, and planned verification objectives. Widths are positive integers or simple parameter references; no arbitrary expressions are evaluated. Entries record whether they came from the user, an accepted assumption, or a future planner proposal. Validation checks identifiers, references, top-level/module structure, known width mismatches, and requirement coverage. It cannot establish that the written specification captures every semantic intent or that a circuit is correct.

### Meta specification drafting

The **Draft specification with Meta** panel uses the provider-independent `RequirementsPlanner` interface in `backend/app/services/interfaces.py`. It sends the saved natural-language request, current structured specification, project description, saved requirements, validation findings, and any answered clarification questions to the existing Meta client. The user may add a planning instruction such as “turn this request into a complete structured specification; ask instead of inventing missing values.”

Enable it only for an explicit live test by setting `SPECIFICATION_PLANNER_ENABLED=true`, providing the backend-only `MODEL_API_KEY`, and restarting FastAPI. The default is disabled and makes zero provider calls. The endpoint is asynchronous and persists queued/running/proposed/accepted/discarded/failed/cancelled/interrupted states with provider-reported usage when available. A bounded correction request is used only for a malformed contract response; application retries are not layered on top of the SDK.

Meta must return a validated proposal containing a complete `CircuitSpecification`, explicit assumptions, clarification questions, module plans, verification objectives, and an optional summary. The backend preserves the original request, binds project/revision identity server-side, runs deterministic validation, and stores the proposal separately. The UI shows the proposed counts, assumptions, questions, validation findings, and JSON before **Accept as draft** is enabled. Accepting creates a new draft revision only if the source revision is still current; discarding leaves the saved specification unchanged. An accepted proposal is not automatically marked ready and does not prove semantic completeness or hardware correctness.

The planner does not generate RTL, call simulation/synthesis tools, or modify files. The current app can still be used entirely offline by leaving the flag disabled and entering the structured fields manually.

## Phase 4 generation workflow

The **RTL proposal generation** section is available in the Specification view. It is disabled by default. A request is accepted only for a saved, immutable ready specification revision with no blocking findings, exactly one top-level module, and complete port interface data. The backend captures the specification revision, selected existing-file context, workspace file hashes, model settings, and timestamps before any provider request.

To deliberately enable the eventual live path, set `GENERATION_ENABLED=true` and provide the backend-only `MODEL_API_KEY`, then restart FastAPI. The default `.env.example` keeps this feature disabled. The UI never asks for or exposes the key, and configuration/status endpoints do not make provider calls. Existing files are included only when the user explicitly selects them as revision context.

The Meta-backed implementation reuses the Phase 2 asynchronous OpenAI-compatible client and returns a typed JSON contract. It requires `rtl/` synthesizable Verilog/SystemVerilog files, `tb/` self-checking finite testbench files, an ordered source manifest, requirement/module and verification/test mappings, assumptions, unresolved questions, limitations, and an implementation summary. The supported HDL subset is conservative synthesizable Verilog/SystemVerilog: no RTL delays, `initial` blocks, file I/O, PLI, external IP, or simulation-only constructs. Contract validation rejects traversal paths, unsupported extensions, duplicates, missing manifest entries, oversized output, top-module mismatches, and unknown specification references. One bounded correction request may be made according to configuration; ambiguous failures are not automatically retried.

Generated files first appear as a persistent proposal. Review the summary, assumptions, unmapped requirements, unresolved questions, and per-file added/modified diff. **Accept proposal** checks that the captured workspace hashes still match, creates recoverable backups for overwritten files, and applies the whole validated set atomically with filesystem rollback on failure. **Discard** changes no active files. A later specification edit cannot change the input revision of an existing generation. Honest labels remain visible: **Generated — not compiled**, **Testbench created — not executed**, and **Synthesis not run**.

Phase 4 has no compilation or simulation runner. Fake providers exist only in offline tests and establish orchestration, persistence, and safety behavior—not RTL design quality or hardware verification. Phase 5 can attach linting, compilation, simulation, synthesis, artifact references, and evidence-backed result reporting to the ordered source manifest and staged proposal.

## Available read-only tools

The model receives only these tools, all bound by backend code to the currently open project:

- `get_project_requirements` — returns saved requirements with a bounded result.
- `list_project_files` — returns deterministic file metadata.
- `read_project_file` — reads an allowlisted UTF-8 text file and supports optional 1-based inclusive line ranges.

The backend validates tool JSON with Pydantic, rejects unknown fields and invalid paths, protects against traversal and symlink escapes, and reports structured tool errors. There are no file-writing, shell, Python, simulation, synthesis, or autonomous-repair tools in Phase 2. File content and tool output are treated as untrusted project data, not as instructions.

## Runtime data

By default, runtime data is stored under `data/`:

- `data/circuitpilot.db` stores projects, requirements, file metadata, project events, conversations, protocol messages, runs, run events, current specification pointers, validation reports, immutable specification revisions, persisted specification planner requests/events/proposals, generation jobs/events, staged proposals, and overwrite backups.
- `data/workspaces/<project-id>/` stores the actual project text files.

The data directory is ignored by Git. Saved assistant-visible messages are persisted, while complete assistant tool-call and matching tool-response protocol records are retained separately for valid future continuation. Interrupted queued/running runs are marked `interrupted` on the next backend startup.

## Checks

Backend tests use real FastAPI requests, SQLite, temporary workspaces, and injected fake providers confined to tests:

```bash
cd backend
uv run pytest -q
```

Frontend type checking and production build:

```bash
cd frontend
npm run typecheck
npm run build
```

The test suite covers project isolation, persistence, tool ordering and binding, malformed/invalid tool arguments, provider error mapping, limits, cancellation, retry, interrupted-run recovery, structured validation, clarification resolution, module/port references, symbolic widths, revision history, readiness gates, revision conflicts, generation contract validation, bounded correction, clarification results, selected-file isolation, staging without workspace mutation, apply/discard, overwrite backups, workspace conflicts, cancellation, and disabled-mode zero-call behavior. Live Meta connectivity is not part of the automated test suite. Phase 3 and Phase 4 verification intentionally perform no provider request; fake providers are confined to tests.

## Phase boundaries

Phase 1 supports persistent project CRUD, requirements, allowlisted text files, activity, and honest empty results. Phase 2 adds the real Meta provider adapter, sanitized configuration status, durable conversations, bounded read-only tool calling, asynchronous runs, cancellation, retry, usage reporting, and restart recovery. Phase 3 adds the offline structured specification editor, deterministic clarifications, module plan, verification plan, JSON export, immutable revisions, readiness review, and the optional Meta proposal/review workflow. Phase 4 adds feature-gated Meta generation, typed RTL/testbench contracts, persistent generation events and proposals, safe review/application, and workspace conflict protection.

The next phase can add lint/compile and simulation tool runners, actual verification evidence, waveform/artifact references, synthesis, and repair loops. Those capabilities remain intentionally disabled here; **Build circuit** is visible but cannot run.
