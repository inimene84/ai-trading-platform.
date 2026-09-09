#!/usr/bin/env bash
# Fold the n8n Assistant sandbox overlay into the existing /docker/n8n stack.
# Does not migrate SQLite → Postgres, does not add a second SearXNG, and does
# not make n8n wait on sandbox-api (production workflows stay up if sandbox fails).
#
# Usage (on the VPS):
#   PROJECT_DIR=/root/ai-trading-platform-v3 ./scripts/apply_n8n_sandbox.sh
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/ai-trading-platform-v3}"
N8N_COMPOSE_DIR="${N8N_COMPOSE_DIR:-/docker/n8n}"
N8N_DATA_DIR="${N8N_DATA_DIR:-/var/lib/docker/volumes/n8n_data/_data}"
OVERLAY_SRC="${PROJECT_DIR}/n8n/docker-compose.sandbox.yml"

if [[ ! -f "${OVERLAY_SRC}" ]]; then
  echo "ERROR: ${OVERLAY_SRC} not found"
  exit 1
fi
if [[ ! -f "${N8N_COMPOSE_DIR}/docker-compose.yml" ]]; then
  echo "ERROR: ${N8N_COMPOSE_DIR}/docker-compose.yml not found"
  exit 1
fi

_upsert_env() {
  local file="$1" key="$2" value="$3"
  if grep -qE "^${key}=" "${file}"; then
    local escaped
    escaped=$(printf '%s' "${value}" | sed -e 's/[&|\\]/\\&/g')
    sed -i "s|^${key}=.*|${key}=${escaped}|" "${file}"
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${file}"
  fi
}

echo "=== n8n sandbox overlay → ${N8N_COMPOSE_DIR} ==="
cp -a "${N8N_COMPOSE_DIR}/docker-compose.yml" \
  "${N8N_COMPOSE_DIR}/docker-compose.yml.bak.$(date +%Y%m%d%H%M%S)"
cp -a "${OVERLAY_SRC}" "${N8N_COMPOSE_DIR}/docker-compose.sandbox.yml"

ENV_FILE="${N8N_COMPOSE_DIR}/.env"
touch "${ENV_FILE}"
chmod 600 "${ENV_FILE}"

_upsert_env "${ENV_FILE}" COMPOSE_FILE "docker-compose.yml:docker-compose.sandbox.yml"

if ! grep -qE '^SANDBOX_API_KEYS=.+$' "${ENV_FILE}"; then
  _upsert_env "${ENV_FILE}" SANDBOX_API_KEYS "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  echo "  generated SANDBOX_API_KEYS"
fi
if ! grep -qE '^SANDBOX_API_RUNNER_REGISTRATION_TOKEN=.+$' "${ENV_FILE}"; then
  _upsert_env "${ENV_FILE}" SANDBOX_API_RUNNER_REGISTRATION_TOKEN "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  echo "  generated SANDBOX_API_RUNNER_REGISTRATION_TOKEN"
fi
if ! grep -qE '^SANDBOX_API_RUNNER_API_KEY=.+$' "${ENV_FILE}"; then
  _upsert_env "${ENV_FILE}" SANDBOX_API_RUNNER_API_KEY "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  echo "  generated SANDBOX_API_RUNNER_API_KEY"
fi

# n8n must present the same key sandbox-api has in SANDBOX_API_KEYS
API_KEY_VALUE="$(grep -E '^SANDBOX_API_KEYS=' "${ENV_FILE}" | cut -d= -f2-)"
_upsert_env "${ENV_FILE}" N8N_SANDBOX_SERVICE_API_KEY "${API_KEY_VALUE}"
_upsert_env "${ENV_FILE}" N8N_INSTANCE_AI_SEARXNG_URL \
  "${N8N_INSTANCE_AI_SEARXNG_URL:-http://ai-trading-searxng:8080}"

if [[ -f "${N8N_DATA_DIR}/database.sqlite" ]]; then
  TS="$(date +%Y%m%d-%H%M%S)"
  cp -p "${N8N_DATA_DIR}/database.sqlite" \
    "${N8N_DATA_DIR}/database.sqlite.pre-sandbox-${TS}"
  echo "  sqlite backup: database.sqlite.pre-sandbox-${TS}"
fi

cd "${N8N_COMPOSE_DIR}"
echo "=== docker compose up (n8n + sandbox, no extra SearXNG) ==="
docker compose up -d

echo "=== wait for sandbox-api healthy ==="
for i in $(seq 1 40); do
  if docker compose ps sandbox-api --format '{{.Health}}' 2>/dev/null | grep -q healthy; then
    echo "  sandbox-api healthy ($i)"
    break
  fi
  echo "  waiting sandbox-api ($i/40)"
  sleep 3
done

echo "=== verify ==="
docker compose ps
echo "--- sandbox-api health from n8n ---"
docker compose exec -T n8n wget -qO- http://sandbox-api:8080/healthz || \
  docker compose exec -T n8n sh -c 'node -e "fetch(\"http://sandbox-api:8080/healthz\").then(r=>r.text()).then(console.log)"'
echo
echo "--- runner registration ---"
docker compose logs sandbox-api 2>&1 | grep -i runner | tail -10 || true
echo "--- n8n healthz ---"
curl -sf http://127.0.0.1:5678/healthz
echo
echo "Done. Set the model in n8n → Settings → AI Assistant (do not set N8N_INSTANCE_AI_MODEL in .env or the UI cannot save the name)."
