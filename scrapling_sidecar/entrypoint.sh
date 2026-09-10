#!/bin/sh
set -eu

TOKEN="${SCRAPLING_MCP_AUTH_TOKEN:-${ADMIN_API_KEY:-}}"
if [ -z "$TOKEN" ]; then
  echo "SCRAPLING_MCP_AUTH_TOKEN or ADMIN_API_KEY is required" >&2
  exit 1
fi

uv run python /opt/scrapling-sidecar/research_api.py &

if [ "${SCRAPLING_INGEST_ENABLED:-false}" = "true" ]; then
  uv run python /opt/scrapling-sidecar/ingest.py &
fi

exec uv run scrapling mcp --http --host 0.0.0.0 --port 8000 --auth-token "$TOKEN"
