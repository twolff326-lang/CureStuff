# Pharma Nexus — Mac Quickstart Guide

Run the entire drug-repurposing platform on your Mac in under 10 minutes.

---

## Step 1: Install Docker Desktop

If you don't already have Docker, install it first.

1. Go to https://www.docker.com/products/docker-desktop/
2. Download **Docker Desktop for Mac** (choose Apple Silicon or Intel based on your chip)
3. Open the `.dmg`, drag Docker to Applications, and launch it
4. Wait for the Docker whale icon to appear in your menu bar and say **"Docker Desktop is running"**

To verify it's working, open Terminal and run:

```bash
docker --version
docker compose version
```

Both should print version numbers without errors.

---

## Step 2: Get an Anthropic API Key (optional but recommended)

The platform works without an API key — you'll still get the full database, scoring, and UI.
But for LLM features (confidence feedback, discovery proposals, report generation), you need a key.

1. Go to https://console.anthropic.com/
2. Sign up or log in
3. Go to **API Keys** → **Create Key**
4. Copy the key (starts with `sk-ant-...`) — you'll paste it in Step 4

---

## Step 3: Clone the Repository

Open Terminal and run:

```bash
cd ~/Desktop
git clone https://github.com/twolff326-lang/CureStuff.git
cd CureStuff/pharma-nexus
```

> If you already have the repo, just `cd` into it:
> ```bash
> cd ~/Desktop/CureStuff/pharma-nexus
> ```

---

## Step 4: Create Your Environment File

```bash
cp .env.example .env
```

Now open `.env` in any text editor (TextEdit, VS Code, nano, etc.) and fill in two things:

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
NCBI_EMAIL=your.email@example.com
```

| Variable | Required? | What it does |
|----------|-----------|-------------|
| `ANTHROPIC_API_KEY` | Optional | Enables LLM confidence scoring, discovery proposals, and report generation |
| `NCBI_EMAIL` | Optional | Identifies you to PubMed's API (polite usage; avoids rate limits) |
| `NCBI_API_KEY` | Optional | Higher PubMed rate limits — get one free at https://www.ncbi.nlm.nih.gov/account/settings/ |
| `LLM_COST_MODE` | Optional | `economy` (default, cheapest), `standard`, or `premium` |

Everything else has working defaults — don't change the database passwords or URLs.

---

## Step 5: Start Everything

```bash
chmod +x start.sh
./start.sh
```

**What happens:**
1. Docker downloads 6 images (Postgres, Redis, Neo4j, Python, Node) — ~2 GB first time
2. Builds the backend and frontend containers
3. Waits for databases to be healthy
4. Runs database migrations automatically
5. Seeds demo data (15 drugs, 8 cancer types, 20 hypotheses)
6. Opens http://localhost:3000 in your browser

**First run takes 3–5 minutes** (mostly downloading images). Subsequent starts take ~30 seconds.

If the script asks for your Anthropic API key and you already put it in `.env`, just press Enter to skip.

---

## Step 6: Explore the Platform

Once you see the green "Pharma Nexus is running!" message, open your browser to:

| URL | What you'll see |
|-----|----------------|
| **http://localhost:3000** | Main dashboard — overview of all hypotheses, scores, evidence strength |
| **http://localhost:3000/hypotheses** | Browse and filter drug-repurposing hypotheses |
| **http://localhost:3000/analysis** | Run LLM confidence scoring, discovery, and expression analysis |
| **http://localhost:3000/discovery** | View LLM-generated novel drug-cancer proposals |
| **http://localhost:3000/knowledge-graph** | Explore the Neo4j-backed knowledge graph |
| **http://localhost:3000/data-sources** | Ingest data from 13 biomedical databases |
| **http://localhost:3000/reports** | Generate PDF reports for any hypothesis |
| **http://localhost:8000/docs** | Interactive API documentation (Swagger) |

### Recommended first walkthrough

1. **Dashboard** — See the overview. Hover over any **(?)** icon to read a plain-English explanation.
2. **Hypotheses** — Click any row to see the detailed breakdown: evidence dimensions, LLM confidence, pathway overlap.
3. **Analysis → LLM Confidence** — Click "Run on all pending" to have Claude evaluate every hypothesis. This uses your API key.
4. **Analysis → LLM Discovery** — Have Claude find novel drug-cancer connections by synthesizing literature. Creates proposals you can review on the Discovery page.
5. **Data Sources** — Click "Start Ingestion" on PubChem, KEGG, or any source that doesn't require a file upload to pull in real data.
6. **Reports** — Pick a hypothesis and generate a PDF report.

---

## Step 7: Stopping and Restarting

### Stop everything

```bash
cd ~/Desktop/CureStuff/pharma-nexus
docker compose down
```

Your data is saved in Docker volumes — nothing is lost.

### Restart later

```bash
cd ~/Desktop/CureStuff/pharma-nexus
docker compose up -d
```

Or run `./start.sh` again — it will skip steps that are already done.

### Full reset (delete all data and start fresh)

```bash
docker compose down -v
./start.sh
```

The `-v` flag removes the database volumes. The next start will recreate everything from scratch.

---

## Troubleshooting

### "Error: Docker daemon is not running"
Open Docker Desktop from your Applications folder and wait for the whale icon in the menu bar.

### "Port 5432 already in use"
You have another Postgres running. Either stop it (`brew services stop postgresql`) or change the port in `.env`:
```
# In docker-compose.yml, change "5432:5432" to "5433:5432"
```

### "Port 3000 already in use"
Something else is on port 3000 (maybe another Node app). Stop it or change the frontend port in `docker-compose.yml`.

### Backend logs show errors
```bash
docker compose logs -f backend
```

### Celery worker (background tasks) logs
```bash
docker compose logs -f celery-worker
```

### Everything is slow on first load
The backend downloads ML models (~400 MB) for sentence embeddings on first use. This happens once and is cached.

### "Failed to fetch" in the UI
The backend might still be starting. Wait 30 seconds and refresh. Check `docker compose ps` — all services should say "healthy".

### Neo4j browser (optional)
You can also explore the graph database directly at http://localhost:7474. Login with `neo4j` / `change_me_in_production`.

---

## Architecture at a Glance

```
┌─────────────┐     ┌──────────────┐     ┌───────────────┐
│  Frontend    │────▶│   Backend    │────▶│  PostgreSQL   │
│  Next.js     │     │   FastAPI    │     │  + pgvector   │
│  :3000       │     │   :8000      │     │  :5432        │
└─────────────┘     └──────┬───────┘     └───────────────┘
                           │
                    ┌──────┴───────┐
                    │              │
              ┌─────▼─────┐  ┌────▼─────┐
              │   Redis    │  │  Neo4j   │
              │  (queues)  │  │ (graph)  │
              │  :6379     │  │  :7687   │
              └─────┬──────┘  └──────────┘
                    │
              ┌─────▼──────┐
              │   Celery   │
              │  (workers) │
              │  async     │
              └────────────┘
```

- **PostgreSQL + pgvector**: Stores drugs, targets, cancers, hypotheses, evidence, embeddings
- **Neo4j**: Knowledge graph for pathway traversal and relationship exploration
- **Redis**: Message broker for Celery task queue
- **Celery**: Runs data ingestion, analysis, and LLM calls as background tasks
- **FastAPI**: REST API with auto-generated Swagger docs
- **Next.js**: React frontend with Tailwind CSS and interactive tooltips

---

## API Keys Summary

| Key | Cost | What it enables |
|-----|------|-----------------|
| Anthropic (`ANTHROPIC_API_KEY`) | Pay-per-use (~$0.01–$0.50 per hypothesis depending on cost mode) | LLM confidence scoring, discovery proposals, report generation |
| NCBI (`NCBI_API_KEY`) | Free | Higher PubMed query rate limits for novelty checking |

The platform is fully functional without any API keys — you just won't be able to use the LLM-powered features.
