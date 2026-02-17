#!/bin/bash
set -e

# Wait for PostgreSQL to be ready
echo "Waiting for PostgreSQL..."
MAX_RETRIES=30
RETRY=0
while [ $RETRY -lt $MAX_RETRIES ]; do
    if python -c "
import psycopg2, os
url = os.environ.get('DATABASE_URL_SYNC', '')
if not url:
    exit(1)
url = url.replace('postgresql+psycopg2://', 'postgresql://')
conn = psycopg2.connect(url)
conn.close()
" 2>/dev/null; then
        echo "PostgreSQL is ready."
        break
    fi
    RETRY=$((RETRY + 1))
    echo "  PostgreSQL not ready, retrying in 2s... ($RETRY/$MAX_RETRIES)"
    sleep 2
done

if [ $RETRY -ge $MAX_RETRIES ]; then
    echo "WARNING: Could not connect to PostgreSQL after $MAX_RETRIES attempts."
    echo "Starting anyway — migrations may fail."
fi

# Run database migrations
echo "Running database migrations..."
cd /app
alembic upgrade head 2>&1 || echo "WARNING: Migrations failed (tables may already exist)"

# Start the main process
echo "Starting: $@"
exec "$@"
