#!/bin/bash
set -e

# Pharma Nexus — Local Development Runner
# Runs postgres in Docker, backend + frontend on host.
# Usage: ./run-dev.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[pharma-nexus]${NC} $1"; }
ok()   { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }

cleanup() {
    log "Shutting down..."
    [ -n "$BACKEND_PID" ] && kill $BACKEND_PID 2>/dev/null
    [ -n "$FRONTEND_PID" ] && kill $FRONTEND_PID 2>/dev/null
    wait 2>/dev/null
    log "Done."
}
trap cleanup EXIT INT TERM

# ---------- 1. Check prerequisites ----------
log "Checking prerequisites..."

if ! command -v docker &>/dev/null; then
    err "Docker not found. Install Docker first: https://docs.docker.com/get-docker/"
    exit 1
fi
ok "Docker found"

if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
    err "Python not found. Install Python 3.11+."
    exit 1
fi
PYTHON=$(command -v python3 || command -v python)
ok "Python found: $($PYTHON --version)"

if ! command -v node &>/dev/null; then
    err "Node.js not found. Install Node.js 18+."
    exit 1
fi
ok "Node found: $(node --version)"

if ! command -v npm &>/dev/null; then
    err "npm not found."
    exit 1
fi
ok "npm found: $(npm --version)"

# ---------- 2. Start Postgres ----------
log "Starting PostgreSQL..."

# Stop existing container if any
docker rm -f pharma-nexus-pg 2>/dev/null || true

docker run -d \
    --name pharma-nexus-pg \
    -e POSTGRES_USER=pharma_nexus \
    -e POSTGRES_PASSWORD=pharma_nexus \
    -e POSTGRES_DB=pharma_nexus \
    -p 5432:5432 \
    postgres:16-alpine \
    >/dev/null

# Wait for postgres
log "Waiting for PostgreSQL to be ready..."
for i in $(seq 1 30); do
    if docker exec pharma-nexus-pg pg_isready -U pharma_nexus &>/dev/null; then
        ok "PostgreSQL is ready"
        break
    fi
    if [ "$i" -eq 30 ]; then
        err "PostgreSQL failed to start. Check: docker logs pharma-nexus-pg"
        exit 1
    fi
    sleep 1
done

# ---------- 3. Install backend dependencies ----------
log "Setting up backend..."
cd "$SCRIPT_DIR/backend"

if ! $PYTHON -c "import fastapi" 2>/dev/null; then
    log "Installing Python dependencies..."
    $PYTHON -m pip install -r requirements.txt -q
fi
ok "Backend dependencies ready"

# ---------- 4. Run migrations ----------
log "Running database migrations..."
export DATABASE_URL="postgresql+asyncpg://pharma_nexus:pharma_nexus@localhost:5432/pharma_nexus"
export DATABASE_URL_SYNC="postgresql+psycopg2://pharma_nexus:pharma_nexus@localhost:5432/pharma_nexus"
export CORS_ORIGINS="http://localhost:3000"
export APP_DEBUG="true"

$PYTHON -m alembic upgrade head
ok "Migrations complete"

# ---------- 5. Start backend ----------
log "Starting backend on http://localhost:8000 ..."
$PYTHON -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Wait for backend to respond
for i in $(seq 1 15); do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        ok "Backend is running"
        break
    fi
    if [ "$i" -eq 15 ]; then
        warn "Backend may still be starting..."
    fi
    sleep 1
done

# ---------- 6. Install frontend dependencies ----------
log "Setting up frontend..."
cd "$SCRIPT_DIR/frontend"

if [ ! -d node_modules ]; then
    log "Installing Node dependencies..."
    npm install
fi
ok "Frontend dependencies ready"

# ---------- 7. Start frontend ----------
log "Starting frontend on http://localhost:3000 ..."
export NEXT_PUBLIC_API_URL="http://localhost:8000"
npm run dev &
FRONTEND_PID=$!

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Pharma Nexus is running!${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "  Frontend:  ${CYAN}http://localhost:3000${NC}"
echo -e "  Backend:   ${CYAN}http://localhost:8000${NC}"
echo -e "  API Docs:  ${CYAN}http://localhost:8000/docs${NC}"
echo -e ""
echo -e "  Press ${YELLOW}Ctrl+C${NC} to stop all services."
echo -e "${GREEN}========================================${NC}"
echo ""

# Keep script alive
wait
