#!/usr/bin/env bash
# Start the Clearline web console (Next.js) on :3000. Logs to var/web.log.
set -euo pipefail
cd "$(dirname "$0")/../apps/web"
mkdir -p ../../var
exec npx next dev -p "${WEB_PORT:-3000}" >> ../../var/web.log 2>&1
