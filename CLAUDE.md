# CLAUDE.md

## Project overview
Pharma-Nexus: a drug repurposing discovery platform. Identifies existing FDA-approved drugs that may work against cancer by combining data from PubChem, ChEMBL, and other biomedical sources.

## Git workflow
- Do NOT create new branches. Always work on the existing branch (check with `git branch` first).
- Pull latest changes before starting work: `git pull origin <current-branch>`
- Commit and push to the current branch directly.
- Never force-push.

## Project structure
- `pharma-nexus/backend/` — Python/FastAPI backend with Celery workers
- `pharma-nexus/frontend/` — Next.js/React/TypeScript frontend with Tailwind CSS
- `pharma-nexus/docker-compose.yml` — Postgres (pgvector), Redis, Neo4j, backend, frontend
- `pharma-nexus/scripts/` — Utility scripts (seeding, analysis)

## Backend
- Framework: FastAPI + SQLAlchemy (async) + Celery + Redis
- Database: PostgreSQL with pgvector extension
- Tests: `cd pharma-nexus/backend && python -m pytest tests/`
- Key directories:
  - `app/api/` — API route handlers
  - `app/models/` — SQLAlchemy ORM models
  - `app/services/` — Business logic (ingestion connectors, scoring, analysis)
  - `app/tasks/` — Celery async tasks

## Frontend
- Framework: Next.js 14 (App Router) + TypeScript + Tailwind CSS
- Build: `cd pharma-nexus/frontend && npm run build`
- Dev: `cd pharma-nexus/frontend && npm run dev`

## Data sources
- PubChem (free, no API key) — chemical properties, drug metadata
- ChEMBL (free, no API key) — drug-target binding affinities, mechanisms
- DrugBank access was denied — the system uses a PubChem fallback (see `backend/app/services/ingestion/drugbank.py` Mode B)

## Running the full stack
```
cd pharma-nexus && docker compose up --build
```
