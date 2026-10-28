# CircuitPilot

CircuitPilot is a local, single-user digital hardware engineering workspace. Phase 1 provides the persistent project foundation needed before connecting a planning or execution agent: project metadata, natural-language requirements, text source files, and real project activity are stored locally and recover after both services restart.

There are no model calls, RTL generation, simulation, synthesis, autonomous repair, fabricated runs, or fabricated verification results in this phase.

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

`backend/uv.lock` and `frontend/package-lock.json` are checked in so both applications have reproducible dependency resolution.

## Configure

Copy `.env.example` to `.env` or export the variables in your shell. The defaults work without a model key. Set `DATABASE_PATH` and `WORKSPACE_ROOT` to absolute paths when running multiple checkouts or starting services from different directories; relative paths are resolved against the repository root.

Important variables:

| Variable | Purpose |
| --- | --- |
| `BACKEND_HOST` / `BACKEND_PORT` | FastAPI bind address and port |
| `NEXT_PUBLIC_API_URL` | Frontend API base URL, normally `http://127.0.0.1:8000/api` |
| `DATABASE_PATH` | SQLite database file |
| `WORKSPACE_ROOT` | Project workspace directories |
| `ALLOWED_ORIGINS` | Comma-separated browser origins allowed by FastAPI CORS |
| `MODEL_API_KEY` / `META_MODEL` | Reserved for the future backend-only provider integration; unused in Phase 1 |

Never put `MODEL_API_KEY` in a `NEXT_PUBLIC_*` variable or in frontend code.

## Run locally

Start the backend in one terminal:

```bash
cd backend
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Start the frontend in another terminal:

```bash
cd frontend
npm run dev
```

Open <http://localhost:3000>. The API health check is at <http://localhost:8000/api/health>.

Runtime data is stored under `data/` by default:

- `data/circuitpilot.db` contains projects, requirements, file metadata, real activity events, and empty future-run tables.
- `data/workspaces/<project-id>/` contains the actual saved text files for each project.

The `data/` contents are ignored by Git.

## Phase 1 behavior

- Create, list, open, rename, and delete projects.
- Save project descriptions and requirements explicitly.
- Create, edit, rename, and delete `.v`, `.sv`, `.vh`, `.svh`, `.md`, `.txt`, and `.json` files.
- Keep project files below their project workspace with traversal, absolute-path, and symlink-escape checks.
- Record real project creation, requirements, metadata, and file activity in SQLite.
- Show loading, empty, success, error, save-state, unsaved-navigation, activity, and honest future-results states.
- Keep run history and results empty until an execution implementation exists.

The backend exposes REST endpoints under `/api` for health, project CRUD, requirements, files, activity, runs, and results. Files are never executed by the application and server filesystem paths are not returned to the browser.

## Checks

Backend tests use real FastAPI requests, SQLite, and temporary filesystem workspaces:

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

## Next phase

The later Meta integration phase can add a backend-only model client, an agent orchestrator, sandboxed tool runner, and event streaming implementation behind the interfaces in `backend/app/services/interfaces.py`. It can then create `Run` and `ArtifactReference` records and populate execution results without changing the Phase 1 project and workspace contracts.

