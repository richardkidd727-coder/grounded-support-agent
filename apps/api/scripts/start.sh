#!/bin/sh
# Container entrypoint: migrate -> index knowledge base -> serve.
# Both setup steps are idempotent (ingest is a no-op when nothing changed), so this is safe on
# every boot - which matters on hosts without a pre-deploy hook (Render free tier).
set -eu

alembic upgrade head
ingest

# exec so uvicorn is PID 1 and receives SIGTERM directly (graceful shutdown drains streams).
exec uvicorn support_agent.main:app \
  --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips='*' \
  --timeout-graceful-shutdown 20 --no-access-log
