# Pharma Nexus

Drug repurposing discovery platform. Identifies existing FDA-approved drugs that may work against cancer by combining data from PubChem, ChEMBL, and curated biomedical sources.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌───────────────┐
│  Next.js    │────>│  FastAPI      │────>│  PostgreSQL   │
│  Frontend   │     │  Backend      │     │  (pgvector)   │
│  :3000      │     │  :8000        │     │  :5432        │
└─────────────┘     └──────────────┘     └───────────────┘
```

**Stack:**
- **Frontend:** Next.js 14, React 18, TypeScript, Tailwind CSS, Recharts
- **Backend:** FastAPI, SQLAlchemy 2.0 (async), Alembic
- **Database:** PostgreSQL with pgvector extension
- **Data sources:** PubChem (free), ChEMBL (free), curated TCGA/Reactome data

## Quick Start

### Prerequisites

- Docker and Docker Compose

### 1. Clone and configure

```bash
git clone <repo-url> && cd pharma-nexus
cp .env.example .env
```

### 2. Start all services

```bash
docker compose up -d --build
```

This launches 3 containers: PostgreSQL, FastAPI backend, and the Next.js frontend.

### 3. Access the application

| Service     | URL                        |
|-------------|----------------------------|
| Frontend    | http://localhost:3000       |
| Backend API | http://localhost:8000       |
| API Docs    | http://localhost:8000/docs  |

### 4. Ingest data

Navigate to **Data Sources** in the UI and trigger ingestion in order:
1. **PubChem** - Fetches ~155 FDA-approved oncology drugs
2. **ChEMBL** - Fetches drug-target binding affinities
3. **Cancer Types** - Seeds 29 TCGA cancer types with mutations
4. **Pathways** - Seeds 20 Reactome cancer pathways
5. **Hypotheses** - Generates scored repurposing hypotheses

Or via API:

```bash
curl -X POST http://localhost:8000/api/ingestion/start \
  -H "Content-Type: application/json" \
  -d '{"source": "pubchem"}'
```

## Hypothesis Engine

The discovery engine uses 3 strategies to identify drug-cancer repurposing candidates:

| Strategy | Description |
|----------|-------------|
| **Direct Target** | Drug targets a gene mutated in the cancer |
| **Pathway Mediated** | Drug targets and cancer mutations share a biological pathway |
| **Clinical Evidence** | Known clinical evidence (approvals, trials) for the drug-cancer pair |

### Scoring Dimensions

| Dimension | Weight | What it measures |
|-----------|--------|------------------|
| Target Binding | 40% | Strength of drug-target binding affinity |
| Pathway Overlap | 35% | Pathway connectivity between drug target and cancer |
| Clinical Evidence | 25% | Known clinical relevance for the pair |

**Composite score** = weighted sum of all 3 dimensions (0-100).

## Frontend Pages

| Page | Description |
|------|-------------|
| **Dashboard** | Stats, top hypotheses, strategy distribution chart |
| **Hypotheses** | Paginated, filterable table with score breakdown |
| **Hypothesis Detail** | Radar chart, score bars, evidence summary |
| **Data Sources** | Trigger ingestion with progress tracking |

## API Endpoints

### Hypotheses
- `GET /api/hypotheses` - Paginated list with filtering and sorting
- `GET /api/hypotheses/top` - Top N by composite score
- `GET /api/hypotheses/stats` - Counts, averages, strategy distribution
- `GET /api/hypotheses/{id}` - Full detail

### Drugs & Cancer Types
- `GET /api/drugs` - Paginated drug list
- `GET /api/drugs/{id}` - Drug detail with targets
- `GET /api/cancer-types` - Paginated cancer type list
- `GET /api/cancer-types/{id}` - Cancer type detail with mutations

### Ingestion
- `POST /api/ingestion/start` - Start ingestion for a data source
- `GET /api/ingestion/status` - Status of all ingestion runs
- `GET /api/ingestion/status/{id}` - Status of a specific run

### Health
- `GET /health` - Health check with DB connectivity

Full interactive API docs: http://localhost:8000/docs

## Development

```bash
# Backend
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend && npm install && npm run dev

# Tests
cd backend && python -m pytest tests/ -v

# Migrations
cd backend && alembic upgrade head
```

## Project Structure

```
pharma-nexus/
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI route handlers
│   │   ├── models/           # SQLAlchemy ORM models (9 tables)
│   │   └── services/         # Hypothesis engine + ingestion connectors
│   ├── alembic/              # Database migrations
│   ├── tests/                # pytest test suite
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── app/              # Next.js pages
│   │   ├── components/       # React components
│   │   ├── lib/              # API client
│   │   └── types/            # TypeScript definitions
│   ├── Dockerfile
│   └── package.json
├── docker-compose.yml
└── .env.example
```

## License

This project is for research and educational purposes.
