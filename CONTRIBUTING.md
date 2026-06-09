# Contributing to OffgridCloud

Thanks for helping out! This document covers local setup and conventions.

## Repository layout

```
backend/    FastAPI app (API + serves the static UI + drives rclone)
frontend/   React + Vite + TypeScript + Tailwind (built to static files)
deploy/     Dockerfile, systemd unit, native install script
docs/        Concept & development plan
assets/      Logo & brand assets
```

## Backend (Python 3.11+)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .          # lint
pytest -q             # tests
uvicorn app.main:app --reload   # http://localhost:8000
```

## Frontend (Node 20+)

```bash
cd frontend
npm install
npm run dev           # http://localhost:5173 (proxies /api to :8000)
npm run build         # production build into dist/
```

For a production-like run, build the frontend and copy `frontend/dist` to
`backend/app/static`; FastAPI then serves the UI from `/` as a single process.

## Conventions

- **Backend:** ruff-clean, type hints, small focused modules.
- **Frontend:** TypeScript strict mode, Tailwind for styling, brand palette
  (`ogc.teal` / `ogc.blue` / `ogc.indigo`).
- **Commits:** clear, imperative messages.
- **Raspberry Pi 3 first:** keep the runtime to one process; avoid adding
  always-on services (Redis, etc.) without a strong reason. Never buffer whole
  media files in RAM — stream to disk.

## Roadmap

See [docs/ENTWICKLUNGSPLAN.md](docs/ENTWICKLUNGSPLAN.md).
