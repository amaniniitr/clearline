#!/usr/bin/env bash
# Start the Clearline API (FastAPI + DBOS) on :8000. Logs to var/api.log.
set -euo pipefail
cd "$(dirname "$0")/../services/core"
mkdir -p ../../var
export PATH="/Users/aman/.local/bin:$PATH"
export PYTHONPATH=.
exec uv run python -W ignore -m uvicorn app.main:app --port "${API_PORT:-8000}" >> ../../var/api.log 2>&1
