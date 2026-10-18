#!/usr/bin/env bash
# Runs the browser E2E suite: a disposable PostgreSQL 16 (migration chain, dry-run check included), the backend (uvicorn) and the
# frontend (Vite), with scripted external AI and e-mail. Usage, from the repository root:
#
#   ./e2e/run.sh            # all journeys
#   ./e2e/run.sh -k retry   # any pytest selection
#
# Requires: node_modules installed (npm ci), PostgreSQL 16 binaries (PG16_BIN, default /usr/lib/postgresql/16/bin), Chromium.
# Output: e2e/artifacts/ receives traces, screenshots, the check list and, on failure, the backend and frontend logs.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}"
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

if [ ! -x node_modules/.bin/vite ]; then
  echo "node_modules/.bin/vite is missing. Run npm ci first." >&2
  exit 2
fi

exec python3 -m pytest e2e -v -p no:cacheprovider "$@"
