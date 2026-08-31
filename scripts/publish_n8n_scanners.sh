#!/usr/bin/env bash
# Publish only safe QuantumTrade scanner workflows (01, 02, 04).
# Keeps 03 Execution Scheduler unpublished until paper fills are validated.
set -euo pipefail

N8N_CONTAINER="${N8N_CONTAINER:-n8n}"

echo "=== Publishing QuantumTrade scanner workflows (01, 02, 04) ==="

mapfile -t WORKFLOW_IDS < <(
  docker run --rm -v n8n_data:/data alpine sh -c \
    "apk add --no-cache sqlite >/dev/null && sqlite3 /data/database.sqlite \
    \"SELECT id FROM workflow_entity WHERE name LIKE '01 - QuantumTrade%' OR name LIKE '02 - QuantumTrade%' OR name LIKE '04 - QuantumTrade%' ORDER BY name;\""
)

if [[ ${#WORKFLOW_IDS[@]} -eq 0 ]]; then
  echo "ERROR: No scanner workflows found. Run import_quantumtrade_n8n_workflows.sh first."
  exit 1
fi

for workflow_id in "${WORKFLOW_IDS[@]}"; do
  echo "Publishing workflow ${workflow_id}..."
  docker exec -u node "${N8N_CONTAINER}" n8n publish:workflow --id="${workflow_id}"
done

echo "=== Ensuring execution scheduler stays unpublished ==="
if [[ "${KEEP_EXECUTOR:-0}" != "1" ]]; then
  EXEC_ID="$(docker run --rm -v n8n_data:/data alpine sh -c \
    "apk add --no-cache sqlite >/dev/null && sqlite3 /data/database.sqlite \
    \"SELECT id FROM workflow_entity WHERE name LIKE '03 - QuantumTrade%' LIMIT 1;\"")"
  if [[ -n "${EXEC_ID}" ]]; then
    docker exec -u node "${N8N_CONTAINER}" n8n unpublish:workflow --id="${EXEC_ID}" 2>/dev/null || true
    echo "Execution scheduler ${EXEC_ID} left unpublished."
  fi
else
  echo "KEEP_EXECUTOR=1 — leaving workflow 03 unchanged."
fi

echo "=== Active QuantumTrade workflows ==="
docker run --rm -v n8n_data:/data alpine sh -c \
  "apk add --no-cache sqlite >/dev/null && sqlite3 /data/database.sqlite \
  \"SELECT id, name, active FROM workflow_entity WHERE name LIKE '%QuantumTrade%' ORDER BY name;\""

echo "Done."
