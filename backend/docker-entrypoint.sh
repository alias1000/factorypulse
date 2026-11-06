#!/bin/sh
set -e
# The API container applies migrations; the worker container sets FP_RUN_MIGRATIONS=false and waits
# until the schema is current (two containers must never migrate concurrently).
if [ "${FP_RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Applying database migrations..."
  alembic upgrade head
fi
exec "$@"
