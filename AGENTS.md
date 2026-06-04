# AGENTS.md

## Cursor Cloud specific instructions

### Product

QuantumTrade Pro: FastAPI backend (`backend/`) + React/Vite frontend (`frontend/`). Default local dev uses SQLite (no Docker required for core API + dashboard).

### Prerequisites (one-time on a fresh VM)

- Python 3.11+ (CI uses 3.12)
- Poetry: `pip install --user poetry` then use `$HOME/.local/bin/poetry` or add `~/.local/bin` to `PATH`
- Node.js 18+ and npm (`frontend/`)

### Dependency install

From repo root:

```bash
poetry install
cd frontend && npm install
```

Copy env if missing: `cp .env.example .env` (`.env` is gitignored; `DRY_RUN_ALL=true` is safe by default).

### Running services (two terminals or tmux panes)

Do **not** run backend and frontend in the same shell pane — the second command replaces the first.

**Backend** (repo root):

```bash
export PATH="$HOME/.local/bin:$PATH"
PYTHONPATH=. poetry run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8080
```

**Frontend**:

```bash
cd frontend && npm run dev
```

Or use `./run.sh` from repo root (starts both; requires `poetry` on `PATH`).

| Service | URL |
|---------|-----|
| Frontend dev server | http://127.0.0.1:5173/ |
| Backend API + Swagger | http://127.0.0.1:8080/docs |

README mentions port 3000; the Express+Vite dev server in `frontend/server.ts` listens on **5173**.

### Lint / test / build

| Check | Command |
|-------|---------|
| Backend tests | `poetry run pytest backend/tests` (from repo root) |
| Frontend typecheck | `cd frontend && npm run lint` (`tsc --noEmit`) |
| Frontend production build | `cd frontend && npm run build` |

Backend CI (`.github/workflows/backend-ci.yml`) mirrors the pytest command above. Frontend `npm run lint` may report existing TypeScript issues in the repo; build (`npm run build`) still succeeds.

### Optional infrastructure

Docker Compose (`docker-compose.yml`) adds LiteLLM, InfluxDB, Qdrant, Redis, nginx. Not required for pytest or basic dashboard/API smoke tests. LLM-backed `/trading/analyze` needs API keys in `.env` or a running LiteLLM proxy.

### Gotchas

- Trading loop may start automatically and log paper trades when the backend boots; this is expected with default config.
- PostgreSQL connection warnings from `frontend/server.ts` are non-fatal; the UI falls back when `DATABASE_URL` is unset.
- `poetry` is not preinstalled on Cloud VMs; install via pip or ensure `~/.local/bin` is on `PATH`.
