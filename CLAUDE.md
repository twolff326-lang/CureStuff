# CLAUDE.md

## Project overview
Pharma-Nexus: a drug repurposing discovery platform. Identifies existing FDA-approved drugs that may work against cancer by combining data from PubChem, ChEMBL, and other biomedical sources.

## Git workflow

> **STRICT BRANCH POLICY — NO EXCEPTIONS**
>
> **NEVER create new branches.** All work — every commit, every feature, every fix — MUST go on the existing branch `add-ingestion-status-bar-dEgae`. Do NOT use `git checkout -b`, `git branch`, `git switch -c`, or any other branch-creation command. There is only one development branch and it is `add-ingestion-status-bar-dEgae`.

- Always confirm you are on `add-ingestion-status-bar-dEgae` before making changes (`git branch`).
- Pull latest changes before starting work: `git pull origin add-ingestion-status-bar-dEgae`
- Commit and push to `add-ingestion-status-bar-dEgae` directly. No PRs to other branches.
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
