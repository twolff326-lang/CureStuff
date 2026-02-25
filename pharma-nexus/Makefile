.PHONY: up down build logs test migrate clean status docs restart-backend

# Start all services
up:
	docker compose up -d

# Stop all services
down:
	docker compose down

# Rebuild and start
build:
	docker compose up -d --build

# View logs (follow)
logs:
	docker compose logs -f --tail=100

# View backend logs only
logs-backend:
	docker compose logs -f --tail=100 backend

# Run database migrations
migrate:
	docker compose exec backend alembic upgrade head

# Run backend tests
test:
	cd backend && python -m pytest tests/ -v

# Check service health
status:
	@docker compose ps
	@echo ""
	@echo "Health check:"
	@curl -s http://localhost:8000/health | python -m json.tool 2>/dev/null || echo "Backend not reachable"

# Stop and remove volumes (DESTRUCTIVE)
clean:
	docker compose down -v

# Open interactive API docs
docs:
	@echo "Opening API docs at http://localhost:8000/docs"
	@which xdg-open > /dev/null 2>&1 && xdg-open http://localhost:8000/docs || open http://localhost:8000/docs 2>/dev/null || echo "Visit http://localhost:8000/docs"

# Quick restart backend only
restart-backend:
	docker compose restart backend
