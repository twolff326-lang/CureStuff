# Pharma Nexus

AI-powered drug repurposing discovery engine. Crosses drug targets, cancer molecular profiles, pathways, literature, and expression data to generate scored repurposing hypotheses.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌───────────────┐
│  Next.js    │────▸│  FastAPI      │────▸│  PostgreSQL   │
│  Frontend   │     │  Backend      │     │  (pgvector)   │
│  :3000      │     │  :8000        │     │  :5432        │
└─────────────┘     └──────┬───────┘     └───────────────┘
                           │
                    ┌──────┴───────┐
                    │              │
               ┌────▾────┐  ┌─────▾─────┐
               │  Redis   │  │  Neo4j     │
               │  :6379   │  │  :7474     │
               └────┬────┘  └───────────┘
                    │
               ┌────▾────────┐
               │  Celery      │
               │  Workers     │
               └─────────────┘
```

**Stack:**
- **Frontend:** Next.js 14, React 18, TypeScript, Tailwind CSS, Recharts
- **Backend:** FastAPI, SQLAlchemy (async), Celery, Pydantic
- **Databases:** PostgreSQL + pgvector (relational + vector search), Neo4j (knowledge graph), Redis (cache + task queue)
- **ML:** sentence-transformers (embeddings), Anthropic Claude (LLM analysis)
- **Data sources:** DrugBank, PubChem, ChEMBL, cBioPortal, TCGA, COSMIC, KEGG, Reactome, STRING, UniProt, OpenTargets, PubMed, ClinicalTrials.gov

## Quick Start

### Prerequisites

- Docker and Docker Compose
- (Optional) Anthropic API key for LLM analysis
- (Optional) NCBI API key for PubMed literature ingestion

### 1. Clone and configure

```bash
git clone <repo-url> && cd pharma-nexus
cp .env.example .env
# Edit .env with your API keys (ANTHROPIC_API_KEY, NCBI_API_KEY, etc.)
```

### 2. Start all services

```bash
docker compose up -d
```

This launches 6 containers: PostgreSQL, Redis, Neo4j, FastAPI backend, Celery worker, and the Next.js frontend.

### 3. Access the application

| Service    | URL                          |
|------------|------------------------------|
| Frontend   | http://localhost:3000         |
| Backend API| http://localhost:8000        |
| API Docs   | http://localhost:8000/docs   |
| Neo4j      | http://localhost:7474        |

### 4. Ingest data

Navigate to **Data Sources** in the UI and trigger ingestion for drug, cancer, pathway, and literature data. The Celery worker processes these asynchronously.

Or via API:

```bash
# Ingest all drug data (DrugBank, PubChem, ChEMBL)
curl -X POST http://localhost:8000/api/ingestion/start \
  -H "Content-Type: application/json" \
  -d '{"source": "all_drugs"}'

# Ingest all cancer data (cBioPortal, TCGA, COSMIC)
curl -X POST http://localhost:8000/api/ingestion/start \
  -H "Content-Type: application/json" \
  -d '{"source": "all_cancer_data"}'

# Generate hypotheses for all cancer types
curl -X POST http://localhost:8000/api/ingestion/start \
  -H "Content-Type: application/json" \
  -d '{"source": "hypotheses_all"}'
```

## Hypothesis Engine

The core discovery engine uses 6 strategies to identify drug-cancer repurposing candidates:

| Strategy | Description |
|----------|-------------|
| **Direct Target** | Drug targets a gene mutated/altered in the cancer |
| **Pathway Mediated** | Drug targets a gene in a cancer-dysregulated pathway |
| **Interaction Network** | Drug target interacts with cancer-altered proteins (STRING PPI) |
| **Expression Driven** | Drug action matches expression changes (inhibitor + overexpression) |
| **Literature Seeded** | PubMed literature mentions drug + cancer in repurposing context |
| **Analog Discovery** | Mechanism-similar drugs (cosine distance on embeddings) to known cancer drugs |

### Scoring Dimensions (6 x 0-100)

Each hypothesis is scored across 6 independent evidence dimensions:

| Dimension | Weight | What it measures |
|-----------|--------|------------------|
| Pathway Overlap | 20% | Shared biological pathways between drug targets and cancer genes |
| Expression Correlation | 20% | Drug target expression compatibility in the cancer type |
| Literature Support | 20% | Published evidence mentioning drug + cancer together |
| Clinical Evidence | 15% | Existing clinical trials for the drug-cancer pair |
| Safety | 10% | Drug approval status, mechanism of action, safety profile |
| Novelty | 15% | Inverse of existing evidence — fewer papers/trials = more novel |

**Composite score** = weighted sum of all 6 dimensions (0-100).

**Evidence strength** mapping: strong (75+), moderate (50-74), suggestive (25-49), speculative (0-24).

Scoring weights are configurable via presets (balanced, novelty-focused, evidence-heavy, clinical-ready) or custom configurations.

## Frontend Pages

| Page | Description |
|------|-------------|
| **Dashboard** | Live stats, top hypotheses, evidence strength distribution, recent ingestion activity |
| **Data Sources** | Trigger data ingestion with real-time progress tracking per source |
| **Hypotheses** | Searchable, filterable table with composite scores and 6-dimension mini-bars |
| **Hypothesis Detail** | Radar chart, dimension breakdowns, evidence records, LLM narrative/critique |
| **Analysis** | Expression z-score vs frequency scatter plot, top over/under-expressed gene bar charts |
| **Knowledge Graph** | Force-directed canvas visualization of drug-target-pathway-cancer relationships |
| **Ingested Data** | Tabbed view of all ingested drugs, targets, cancer types, pathways, literature |

## API Endpoints

### Hypotheses
- `GET /api/hypotheses/` — Paginated list with filtering and sorting
- `GET /api/hypotheses/top` — Top N by composite score
- `GET /api/hypotheses/novel` — High novelty + composite candidates
- `GET /api/hypotheses/stats` — Counts, averages, distributions
- `GET /api/hypotheses/{id}` — Full detail with evidence
- `POST /api/hypotheses/generate/cancer/{id}` — Generate for a cancer type
- `POST /api/hypotheses/generate/all` — Generate for all cancers
- `POST /api/hypotheses/rescore` — Rescore all with new weights

### Ingestion
- `POST /api/ingestion/start` — Start ingestion task
- `GET /api/ingestion/status/{task_id}` — Check task progress
- `GET /api/ingestion/record-counts` — Row counts for all tables
- `GET /api/ingestion/live-status` — Most recent log per source

### Analysis
- `GET /api/analysis/expression/{cancer_type_id}/top-genes` — Top differentially expressed genes
- `GET /api/knowledge-graph/stats` — Graph node/edge counts
- `GET /api/knowledge-graph/drug/{id}/neighborhood` — Drug neighborhood subgraph
- `GET /api/knowledge-graph/cancer/{code}/neighborhood` — Cancer neighborhood subgraph

Full interactive API docs: http://localhost:8000/docs

## Development

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Running Tests

```bash
cd backend
pip install pytest pytest-asyncio
python -m pytest tests/ -v
```

**Test coverage:** 116 tests across 3 modules:
- `test_scoring_config.py` — Weight validation, composite scoring, strength mapping
- `test_evidence_scorer.py` — All 6 scoring dimensions with mocked DB
- `test_hypothesis_engine.py` — Summary generation, rescoring, integration

### Database Migrations

```bash
cd backend
alembic upgrade head
```

## Project Structure

```
pharma-nexus/
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI route handlers
│   │   ├── models/           # SQLAlchemy ORM models (20+ tables)
│   │   ├── services/         # Business logic
│   │   │   ├── evidence_scorer.py    # 6-dimension scoring engine
│   │   │   ├── hypothesis_engine.py  # 6-strategy discovery engine
│   │   │   ├── scoring_config.py     # Weight management
│   │   │   ├── pathway_analyzer.py   # Pathway overlap analysis
│   │   │   └── ingestion/            # Data source connectors
│   │   └── tasks/            # Celery async tasks
│   ├── alembic/              # Database migrations
│   ├── tests/                # pytest test suite
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── app/              # Next.js pages
│   │   ├── components/       # React components
│   │   ├── lib/              # API client, utilities
│   │   └── types/            # TypeScript definitions
│   ├── Dockerfile
│   └── package.json
├── neo4j/                    # Graph DB initialization
├── scripts/                  # Seed data, analysis runners
├── docker-compose.yml
└── .env.example
```

## License

This project is for research and educational purposes.
