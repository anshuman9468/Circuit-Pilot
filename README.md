# CircuitPilot

CircuitPilot is a local, single-user digital hardware engineering workspace. Phase 2 adds a real, backend-only Meta model connection and durable project conversations on top of the Phase 1 project, requirements, and source-file foundation.

The assistant can discuss a project and use three read-only, project-bound tools to inspect saved requirements and text files. It does not generate RTL, write files, execute commands, simulate, synthesize, or claim verification results yet.

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
| `META_MODEL` | Defaults to `muse-spark-1.3` |
| `MODEL_TIMEOUT_SECONDS` | Provider request timeout |
| `MAX_MODEL_ROUNDS` | Maximum model turns in one bounded run |
| `MAX_TOOL_CALLS` | Maximum read-only tool calls across one run |
| `MAX_COMPLETION_TOKENS` | `max_completion_tokens` sent to Chat Completions |
| `MAX_CONTEXT_CHARS` | Maximum safely reconstructed provider context |
| `MAX_TOOL_RESULT_CHARS` | Maximum returned requirements/file tool content |
| `MAX_ACTIVE_RUNS` | Maximum simultaneous active runs for this backend process |
| `MAX_USER_MESSAGE_CHARS` | Maximum submitted conversation message size |

The Phase 2 provider contract follows Meta's current documentation:

- [Meta Chat Completions quickstart](https://dev.meta.ai/docs/cookbook/quickstart-chat-completions)
- [Meta Chat Completions protocol](https://dev.meta.ai/docs/protocols/chat-completions)
- [Meta tool calling](https://dev.meta.ai/docs/tool-calling)

The implementation uses the OpenAI Python SDK's asynchronous client, `https://api.meta.ai/v1`, `muse-spark-1.3`, function tools, `tool_choice: auto`, and the documented Chat Completions parameter `max_completion_tokens`. It deliberately disables SDK retries and does not add application retries, avoiding duplicate billable requests.

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

## Available read-only tools

The model receives only these tools, all bound by backend code to the currently open project:

- `get_project_requirements` — returns saved requirements with a bounded result.
- `list_project_files` — returns deterministic file metadata.
- `read_project_file` — reads an allowlisted UTF-8 text file and supports optional 1-based inclusive line ranges.

The backend validates tool JSON with Pydantic, rejects unknown fields and invalid paths, protects against traversal and symlink escapes, and reports structured tool errors. There are no file-writing, shell, Python, simulation, synthesis, or autonomous-repair tools in Phase 2. File content and tool output are treated as untrusted project data, not as instructions.

## Runtime data

By default, runtime data is stored under `data/`:

- `data/circuitpilot.db` stores projects, requirements, file metadata, project events, conversations, protocol messages, runs, and run events.
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

The test suite covers project isolation, persistence, tool ordering and binding, malformed/invalid tool arguments, provider error mapping, limits, cancellation, retry, and interrupted-run recovery. Live Meta connectivity is not part of the automated test suite; run the explicit UI test only after configuring a real backend key.

## Phase boundaries

Phase 1 supports persistent project CRUD, requirements, allowlisted text files, activity, and honest empty results. Phase 2 adds the real Meta provider adapter, sanitized configuration status, durable conversations, bounded read-only tool calling, asynchronous runs, cancellation, retry, usage reporting, and restart recovery.

The next phase can add carefully sandboxed RTL generation and file-writing tools, followed by tool runners for simulation and synthesis, artifact references, actual verification evidence, and autonomous repair. Those capabilities remain intentionally disabled here; **Build circuit** is visible but cannot run.
