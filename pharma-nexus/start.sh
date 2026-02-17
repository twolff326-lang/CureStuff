#!/bin/bash
set -e

# ============================================
# Pharma Nexus — One-Command Startup
# ============================================
# Usage: ./start.sh
#
# This will:
#   1. Create .env from .env.example if it doesn't exist
#   2. Prompt for your Anthropic API key if not set
#   3. Start all services (Postgres, Redis, Neo4j, Backend, Celery, Frontend)
#   4. Auto-run database migrations
#   5. Open the app in your browser
#
# Prerequisites: Docker and Docker Compose
# ============================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}"
echo "  ╔═══════════════════════════════════════╗"
echo "  ║         PHARMA NEXUS                  ║"
echo "  ║   Drug Repurposing Discovery Engine   ║"
echo "  ╚═══════════════════════════════════════╝"
echo -e "${NC}"

# Check Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker is not installed.${NC}"
    echo "Install Docker: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! docker compose version &> /dev/null && ! docker-compose version &> /dev/null; then
    echo -e "${RED}Error: Docker Compose is not installed.${NC}"
    exit 1
fi

# Determine compose command
if docker compose version &> /dev/null; then
    COMPOSE="docker compose"
else
    COMPOSE="docker-compose"
fi

# Create .env if missing
if [ ! -f .env ]; then
    echo -e "${YELLOW}Creating .env from .env.example...${NC}"
    cp .env.example .env
    echo -e "${GREEN}Created .env${NC}"
fi

# Check for Anthropic API key
source .env 2>/dev/null || true
if [ -z "$ANTHROPIC_API_KEY" ] || [ "$ANTHROPIC_API_KEY" = "your_anthropic_api_key_here" ]; then
    echo ""
    echo -e "${YELLOW}Anthropic API key not found.${NC}"
    echo "The system works without it (hypotheses generate from structured data),"
    echo "but LLM features (confidence feedback, discovery) need it."
    echo ""
    read -p "Enter your Anthropic API key (or press Enter to skip): " api_key
    if [ -n "$api_key" ]; then
        # Use | as sed delimiter to avoid issues with special chars in keys
        sed -i "s|ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${api_key}|" .env
        echo -e "${GREEN}API key saved to .env${NC}"
    else
        echo -e "${YELLOW}Skipping — LLM features will be disabled.${NC}"
    fi
fi

echo ""
echo -e "${BLUE}Starting services...${NC}"
echo "  This takes 1-2 minutes on first run (building images)."
echo ""

# Start everything
$COMPOSE up -d --build

echo ""
echo -e "${GREEN}All services starting!${NC}"
echo ""
echo "  Waiting for health checks..."

# Wait for backend to be healthy (up to 90 seconds)
TRIES=0
MAX_TRIES=30
while [ $TRIES -lt $MAX_TRIES ]; do
    if $COMPOSE ps backend 2>/dev/null | grep -q "healthy"; then
        break
    fi
    TRIES=$((TRIES + 1))
    sleep 3
done

if [ $TRIES -ge $MAX_TRIES ]; then
    echo -e "${YELLOW}Backend is still starting. Check logs with:${NC}"
    echo "  $COMPOSE logs -f backend"
else
    echo -e "${GREEN}Backend is healthy.${NC}"

    # Auto-seed demo data if database is empty
    echo "  Loading demo data..."
    SEED_RESULT=$(curl -s -X POST http://localhost:8000/api/seed 2>/dev/null || echo '{"status":"error"}')
    if echo "$SEED_RESULT" | grep -q '"seeded"'; then
        echo -e "  ${GREEN}Demo data loaded (15 drugs, 8 cancers, 20 hypotheses).${NC}"
    elif echo "$SEED_RESULT" | grep -q '"skipped"'; then
        echo -e "  ${YELLOW}Database already has data — skipping seed.${NC}"
    else
        echo -e "  ${YELLOW}Could not auto-seed. Click 'Load Demo Data' on the dashboard.${NC}"
    fi
fi

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Pharma Nexus is running!                 ║${NC}"
echo -e "${GREEN}╠═══════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  Frontend:  http://localhost:3000          ║${NC}"
echo -e "${GREEN}║  Backend:   http://localhost:8000          ║${NC}"
echo -e "${GREEN}║  API docs:  http://localhost:8000/docs     ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════╝${NC}"
echo ""
echo "Quick start:"
echo "  1. Open http://localhost:3000 — demo data is already loaded"
echo "  2. Browse Hypotheses to see pre-scored drug repurposing candidates"
echo "  3. Go to Analysis → LLM Confidence to activate the feedback loop"
echo "  4. Go to Analysis → LLM Discovery to find novel connections"
echo ""
echo "Useful commands:"
echo "  $COMPOSE logs -f backend    # Backend logs"
echo "  $COMPOSE logs -f celery-worker  # Task logs"
echo "  $COMPOSE down               # Stop everything"
echo ""

# Try to open browser
if command -v xdg-open &> /dev/null; then
    xdg-open http://localhost:3000 2>/dev/null &
elif command -v open &> /dev/null; then
    open http://localhost:3000 2>/dev/null &
fi
