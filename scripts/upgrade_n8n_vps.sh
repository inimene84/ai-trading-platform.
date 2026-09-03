#!/usr/bin/env bash
# Upgrade n8n on the Hostinger VPS (/docker/n8n stack).
# Backs up SQLite, pulls target image, recreates container, verifies version.
#
# Usage:
#   N8N_TARGET_VERSION=2.36.8 ./scripts/upgrade_n8n_vps.sh
# Run on the VPS (or via ssh_vps_remote.sh wrapper).
set -euo pipefail

N8N_COMPOSE_DIR="${N8N_COMPOSE_DIR:-/docker/n8n}"
N8N_TARGET_VERSION="${N8N_TARGET_VERSION:-2.36.8}"
N8N_IMAGE="docker.n8n.io/n8nio/n8n:${N8N_TARGET_VERSION}"
N8N_DATA_DIR="${N8N_DATA_DIR:-/var/lib/docker/volumes/n8n_data/_data}"
TRADING_ENV="${TRADING_ENV:-/root/ai-trading-platform-v3/.env}"

if [[ ! -f "${N8N_COMPOSE_DIR}/docker-compose.yml" ]]; then
  echo "ERROR: ${N8N_COMPOSE_DIR}/docker-compose.yml not found"
  exit 1
fi

echo "=== n8n upgrade: target ${N8N_IMAGE} ==="
cd "${N8N_COMPOSE_DIR}"

CURRENT="$(docker exec n8n n8n --version 2>/dev/null || echo unknown)"
echo "Current version: ${CURRENT}"

if [[ -f "${TRADING_ENV}" ]]; then
  # Inject backend API key for QuantumTrade workflow HTTP nodes ($env.BACKEND_API_KEY)
  set -a
  # shellcheck disable=SC1090
  source "${TRADING_ENV}"
  set +a
  export BACKEND_API_KEY="${ADMIN_API_KEY:-${API_AUTH_TOKEN:-${BACKEND_API_KEY:-}}}"
  # Compose reads this file on every later restart, avoiding a blank key when
  # the upgrade shell is no longer present.
  printf 'BACKEND_API_KEY=%s\n' "${BACKEND_API_KEY}" > "${N8N_COMPOSE_DIR}/.env"
  chmod 600 "${N8N_COMPOSE_DIR}/.env"
fi

echo "=== Backing up database.sqlite ==="
TS="$(date +%Y%m%d-%H%M%S)"
if [[ -f "${N8N_DATA_DIR}/database.sqlite" ]]; then
  cp -p "${N8N_DATA_DIR}/database.sqlite" "${N8N_DATA_DIR}/database.sqlite.pre-upgrade-${TS}"
  echo "Backup: ${N8N_DATA_DIR}/database.sqlite.pre-upgrade-${TS}"
fi

echo "=== Pinning compose image to ${N8N_IMAGE} ==="
sed -i "s|image: docker.n8n.io/n8nio/n8n:.*|image: ${N8N_IMAGE}|" docker-compose.yml

# Ensure BACKEND_API_KEY is passed into n8n (for workflow header auth expressions)
if ! grep -q 'BACKEND_API_KEY' docker-compose.yml; then
  sed -i '/N8N_CONCURRENCY_PROCESSES/a\      - BACKEND_API_KEY=${BACKEND_API_KEY}' docker-compose.yml
fi

# Keep execution storage bounded and allow internal workflows to read the
# backend key. N8N_PROXY_HOPS fixes rate-limit client IP detection via Traefik.
declare -a N8N_SETTINGS=(
  'N8N_BLOCK_ENV_ACCESS_IN_NODE=false'
  'N8N_PROXY_HOPS=1'
  'EXECUTIONS_DATA_PRUNE=true'
  'EXECUTIONS_DATA_MAX_AGE=168'
  'EXECUTIONS_DATA_PRUNE_MAX_COUNT=1000'
)
for setting in "${N8N_SETTINGS[@]}"; do
  name="${setting%%=*}"
  if ! grep -q "${name}=" docker-compose.yml; then
    sed -i "/N8N_CONCURRENCY_PROCESSES/a\\      - ${setting}" docker-compose.yml
  fi
done

echo "=== Pulling image and recreating n8n ==="
docker compose pull n8n
docker compose up -d n8n

echo "=== Waiting for n8n startup ==="
for i in $(seq 1 30); do
  if docker exec n8n n8n --version >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

NEW="$(docker exec n8n n8n --version)"
echo "Upgraded: ${CURRENT} -> ${NEW}"
docker inspect n8n --format 'Status: {{.State.Status}}  RestartCount: {{.RestartCount}}'

echo "=== Done. Open https://n8n1.thorinvest.org and confirm editor loads. ==="
