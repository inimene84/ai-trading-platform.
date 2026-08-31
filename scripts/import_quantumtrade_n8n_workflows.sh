#!/usr/bin/env bash
# Import the 3 QuantumTrade workflow JSON files into n8n and optionally activate them.
#
# Files (repo):
#   workflows/01_market_scanner_workflow.json
#   workflows/02_news_macro_scanner_workflow.json
#   workflows/03_execution_scheduler_workflow.json
#
# Usage (on VPS):
#   PROJECT_DIR=/root/ai-trading-platform-v3 ./scripts/import_quantumtrade_n8n_workflows.sh
#   ACTIVATE=1 ./scripts/import_quantumtrade_n8n_workflows.sh   # publish after validation
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/ai-trading-platform-v3}"
N8N_CONTAINER="${N8N_CONTAINER:-n8n}"
WORKFLOW_DIR="${PROJECT_DIR}/workflows"
# Safe default: import as drafts. Publishing the execution scheduler can place
# orders, so it must be an explicit action after a successful paper test.
ACTIVATE="${ACTIVATE:-0}"

if [[ ! -d "${WORKFLOW_DIR}" ]]; then
  echo "ERROR: ${WORKFLOW_DIR} not found"
  exit 1
fi

echo "=== Importing QuantumTrade n8n workflows from ${WORKFLOW_DIR} ==="

PROJECT_ID="${N8N_PROJECT_ID:-}"
if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID="$(docker run --rm -v n8n_data:/data alpine sh -c \
    "apk add --no-cache sqlite >/dev/null && sqlite3 /data/database.sqlite \"SELECT id FROM project WHERE type='personal' LIMIT 1;\"")"
fi
echo "Using projectId: ${PROJECT_ID}"

import_one() {
  local src="$1"
  local base
  base="$(basename "${src}")"
  local tmp="/tmp/n8n-import-${base}"
  local workflow_name existing_id
  workflow_name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "${src}")"
  existing_id="$(docker run --rm -v n8n_data:/data alpine sh -c \
    "apk add --no-cache sqlite >/dev/null && sqlite3 /data/database.sqlite \"SELECT id FROM workflow_entity WHERE name='${workflow_name}' ORDER BY rowid DESC LIMIT 1;\"")"
  echo "--- Import ${base} ---"

  # n8n 2.36 expects export format: [{ id, name, nodes, ... }] with workflow id set
  python3 - "${src}" "${tmp}" "${existing_id}" <<'PY'
import json, secrets, string, sys
src, dst, existing_id = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src) as f:
    data = json.load(f)
if isinstance(data, list):
    wf = data[0]
else:
    wf = data
    data = [wf]
if existing_id:
    wf["id"] = existing_id
elif not wf.get("id"):
    alphabet = string.ascii_letters + string.digits
    wf["id"] = "".join(secrets.choice(alphabet) for _ in range(16))
wf.setdefault("active", False)
wf.setdefault("isArchived", False)
with open(dst, "w") as f:
    json.dump(data, f)
PY

  docker cp "${tmp}" "${N8N_CONTAINER}:/tmp/${base}"
  docker exec -u node "${N8N_CONTAINER}" n8n import:workflow \
    --input="/tmp/${base}" \
    --projectId="${PROJECT_ID}"
  docker exec "${N8N_CONTAINER}" rm -f "/tmp/${base}" 2>/dev/null || true
  rm -f "${tmp}"
}

for f in \
  "${WORKFLOW_DIR}/01_market_scanner_workflow.json" \
  "${WORKFLOW_DIR}/02_news_macro_scanner_workflow.json" \
  "${WORKFLOW_DIR}/03_execution_scheduler_workflow.json" \
  "${WORKFLOW_DIR}/04_forex_scanner_workflow.json"
do
  if [[ ! -f "${f}" ]]; then
    echo "ERROR: missing ${f}"
    exit 1
  fi
  import_one "${f}"
done

echo "=== Imported workflows in n8n ==="
docker run --rm -v n8n_data:/data alpine sh -c \
  "apk add --no-cache sqlite >/dev/null && sqlite3 /data/database.sqlite \"SELECT id, name, active FROM workflow_entity WHERE name LIKE '%QuantumTrade%' ORDER BY name;\""

if [[ "${ACTIVATE}" == "1" ]]; then
  echo "=== Publishing QuantumTrade workflows ==="
  mapfile -t WORKFLOW_IDS < <(
    docker run --rm -v n8n_data:/data alpine sh -c \
      "apk add --no-cache sqlite >/dev/null && sqlite3 /data/database.sqlite \"SELECT id FROM workflow_entity WHERE name LIKE '%QuantumTrade%' ORDER BY name;\""
  )
  for workflow_id in "${WORKFLOW_IDS[@]}"; do
    docker exec -u node "${N8N_CONTAINER}" n8n publish:workflow --id="${workflow_id}"
  done
fi

echo "=== Test scan-markets from inside n8n (needs BACKEND_API_KEY in n8n env) ==="
docker exec "${N8N_CONTAINER}" sh -c '
  if [ -z "${BACKEND_API_KEY:-}" ]; then
    echo "WARN: BACKEND_API_KEY not set in n8n container — POST workflows will 401 until set"
    exit 0
  fi
  wget -qO- --timeout=10 \
    --header="Content-Type: application/json" \
    --header="X-API-Key: ${BACKEND_API_KEY}" \
    --post-data="{\"universe\":[\"EURUSD\"],\"timeframe\":\"M5\"}" \
    http://backend:8000/api/signals/scan-markets | head -c 300
  echo
'

echo "Done. In n8n UI search: QuantumTrade"
