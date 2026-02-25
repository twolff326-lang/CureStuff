#!/bin/bash

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
    echo ""
    log "Shutting down..."
    [ -n "$BACKEND_PID" ] && kill $BACKEND_PID 2>/dev/null
    [ -n "$FRONTEND_PID" ] && kill $FRONTEND_PID 2>/dev/null
    docker rm -f pharma-nexus-pg 2>/dev/null || true
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

PYTHON=""
if command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    err "Python not found. Install Python 3.11+."
    exit 1
fi
ok "Python found: $($PYTHON --version 2>&1)"

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

# ---------- 2. Free port 5432 and start Postgres ----------
log "Starting PostgreSQL..."

# Kill any existing pharma-nexus postgres container
docker rm -f pharma-nexus-pg 2>/dev/null || true

# Also stop any docker compose postgres that might hold port 5432
docker rm -f pharma-nexus-postgres 2>/dev/null || true

# Check if port 5432 is already taken
if lsof -i :5432 >/dev/null 2>&1 || nc -z localhost 5432 2>/dev/null; then
    warn "Port 5432 is already in use."
    warn "Trying to use existing PostgreSQL on localhost:5432..."
    # Try connecting to whatever is on 5432
    PGREADY=false
    for i in $(seq 1 5); do
        if docker exec pharma-nexus-pg pg_isready -U pharma_nexus &>/dev/null 2>&1; then
            PGREADY=true
            break
        fi
        # Maybe it's a host-native postgres — just try to proceed
        sleep 1
    done
    if [ "$PGREADY" = true ]; then
        ok "Using existing PostgreSQL container"
    else
        warn "Something is on port 5432. Will try to proceed anyway."
        warn "If migrations fail, stop whatever is using port 5432 and re-run."
    fi
else
    # Port is free, start fresh postgres
    log "Pulling postgres image (first time may take a minute)..."
    if ! docker run -d \
        --name pharma-nexus-pg \
        -e POSTGRES_USER=pharma_nexus \
        -e POSTGRES_PASSWORD=pharma_nexus \
        -e POSTGRES_DB=pharma_nexus \
        -p 5432:5432 \
        postgres:16-alpine; then
        err "Failed to start PostgreSQL container."
        err "Check that Docker is running: docker info"
        exit 1
    fi

    log "Waiting for PostgreSQL to accept connections..."
    for i in $(seq 1 30); do
        if docker exec pharma-nexus-pg pg_isready -U pharma_nexus >/dev/null 2>&1; then
            ok "PostgreSQL is ready"
            break
        fi
        if [ "$i" -eq 30 ]; then
            err "PostgreSQL did not become ready in 30s."
            err "Check logs: docker logs pharma-nexus-pg"
            exit 1
        fi
        echo -e "  ...waiting ($i/30)"
        sleep 1
    done
fi

# ---------- 3. Install backend dependencies ----------
log "Setting up backend..."
cd "$SCRIPT_DIR/backend"

if ! $PYTHON -c "import fastapi" 2>/dev/null; then
    log "Installing Python dependencies..."
    $PYTHON -m pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        err "Failed to install Python dependencies"
        exit 1
    fi
fi
ok "Backend dependencies ready"

# ---------- 4. Run migrations ----------
log "Running database migrations..."
export DATABASE_URL="postgresql+asyncpg://pharma_nexus:pharma_nexus@localhost:5432/pharma_nexus"
export DATABASE_URL_SYNC="postgresql+psycopg2://pharma_nexus:pharma_nexus@localhost:5432/pharma_nexus"
export CORS_ORIGINS="http://localhost:3000"
export APP_DEBUG="true"

if ! $PYTHON -m alembic upgrade head; then
    err "Database migrations failed."
    err "Check that PostgreSQL is running and accessible on localhost:5432"
    exit 1
fi
ok "Migrations complete"

# ---------- 5. Start backend ----------
log "Starting backend on http://localhost:8000 ..."
$PYTHON -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

for i in $(seq 1 15); do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        ok "Backend is running at http://localhost:8000"
        break
    fi
    if [ "$i" -eq 15 ]; then
        warn "Backend may still be starting (check above for errors)..."
    fi
    sleep 1
done

# ---------- 6. Install frontend dependencies ----------
log "Setting up frontend..."
cd "$SCRIPT_DIR/frontend"

if [ ! -d node_modules ]; then
    log "Installing Node dependencies (this may take a minute)..."
    if ! npm install; then
        err "Failed to install Node dependencies"
        exit 1
    fi
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

wait
