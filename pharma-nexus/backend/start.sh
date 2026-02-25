#!/bin/bash
set -e

echo "=== Pharma Nexus Backend ==="
echo "Waiting for PostgreSQL at postgres:5432..."

# Wait for postgres to accept connections (up to 30 seconds)
for i in $(seq 1 30); do
    if python -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(1)
try:
    s.connect(('postgres', 5432))
    s.close()
    exit(0)
except:
    exit(1)
" 2>/dev/null; then
        echo "PostgreSQL is ready."
        break
    fi
    echo "  ...waiting ($i/30)"
    sleep 1
done

echo "Running database migrations..."
alembic upgrade head
echo "Migrations complete."

echo "Starting uvicorn on port 8000..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 "$@"
