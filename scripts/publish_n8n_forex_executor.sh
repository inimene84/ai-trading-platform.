#!/usr/bin/env bash
# Publish the forex-only execution scheduler (workflow 03) with guards enabled in backend.
# Run after clearing the candidate queue and confirming cTrader is connected.
set -euo pipefail

N8N_CONTAINER="${N8N_CONTAINER:-n8n}"

echo "=== Publishing QuantumTrade forex execution scheduler (03) ==="

EXEC_ID="$(docker run --rm -v n8n_data:/data alpine sh -c \
  "apk add --no-cache sqlite >/dev/null && sqlite3 /data/database.sqlite \
  \"SELECT id FROM workflow_entity WHERE name LIKE '03 - QuantumTrade%' LIMIT 1;\"")"

if [[ -z "${EXEC_ID}" ]]; then
  echo "ERROR: workflow 03 not found. Run import_quantumtrade_n8n_workflows.sh first."
  exit 1
fi

docker exec -u node "${N8N_CONTAINER}" n8n publish:workflow --id="${EXEC_ID}"
docker restart "${N8N_CONTAINER}" >/dev/null
sleep 8

echo "=== QuantumTrade workflows ==="
docker run --rm -v n8n_data:/data alpine sh -c \
  "apk add --no-cache sqlite >/dev/null && sqlite3 /data/database.sqlite \
  \"SELECT name, active FROM workflow_entity WHERE name LIKE '%QuantumTrade%' ORDER BY name;\""

echo "Done. Scheduler polls: ready-for-execution?broker=ctrader&forex_only=true&limit=10"
