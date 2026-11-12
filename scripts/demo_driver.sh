#!/usr/bin/env bash
# Clearline 5-Minute Demo Automation Driver
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CORE_DIR="$PROJECT_ROOT/services/core"

export PLAYWRIGHT_BROWSERS_PATH="${HOME}/Library/Caches/ms-playwright"

cd "$CORE_DIR"
exec "$CORE_DIR/.venv/bin/python" "$SCRIPT_DIR/demo_driver.py"
