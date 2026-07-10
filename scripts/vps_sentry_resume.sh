#!/usr/bin/env bash
set -euo pipefail
TOKEN="${SENTRY_WATCHDOG_TOKEN:?set SENTRY_WATCHDOG_TOKEN}"
curl -sf -X POST "http://127.0.0.1:8001/sentry/resume" \
  -H "Content-Type: application/json" \
  -H "X-Sentry-Token: ${TOKEN}" \
  -d '{"note":"post-deploy resume"}'
echo
curl -sf "http://127.0.0.1:8001/sentry/status"
echo
