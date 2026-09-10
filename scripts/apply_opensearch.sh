#!/usr/bin/env bash
# Start internal OpenSearch + Dashboards on trading-net and point n8n at it.
# Does not rebuild the trading stack or publish 9200/5601 past loopback.
#
# Usage (on the VPS):
#   PROJECT_DIR=/root/ai-trading-platform-v3 ./scripts/apply_opensearch.sh
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/ai-trading-platform-v3}"
COMPOSE_SRC="${PROJECT_DIR}/opensearch/docker-compose.yml"
COMPOSE_DIR="${OPENSEARCH_COMPOSE_DIR:-/docker/opensearch}"
N8N_CONTAINER="${N8N_CONTAINER:-n8n}"

if [[ ! -f "${COMPOSE_SRC}" ]]; then
  echo "ERROR: ${COMPOSE_SRC} not found"
  exit 1
fi

if ! docker network inspect trading-net >/dev/null 2>&1; then
  echo "ERROR: docker network trading-net is missing"
  exit 1
fi

current_map="$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)"
if [[ "${current_map}" -lt 262144 ]]; then
  echo "=== raising vm.max_map_count (OpenSearch mmap) ==="
  sysctl -w vm.max_map_count=262144
  if [[ -d /etc/sysctl.d ]]; then
    echo "vm.max_map_count=262144" >/etc/sysctl.d/99-opensearch.conf
  fi
fi

echo "=== install compose → ${COMPOSE_DIR} ==="
mkdir -p "${COMPOSE_DIR}"
cp -a "${COMPOSE_SRC}" "${COMPOSE_DIR}/docker-compose.yml"
cd "${COMPOSE_DIR}"
docker compose pull
docker compose up -d

echo "=== wait for OpenSearch ==="
ok=0
for i in $(seq 1 40); do
  if curl -sf http://127.0.0.1:9200 >/dev/null; then
    ok=1
    break
  fi
  sleep 3
done
if [[ "${ok}" -ne 1 ]]; then
  echo "ERROR: OpenSearch did not become ready on 127.0.0.1:9200"
  docker compose logs --tail 80 opensearch
  exit 1
fi
curl -sS http://127.0.0.1:9200 | head -c 400
echo

if docker inspect "${N8N_CONTAINER}" >/dev/null 2>&1; then
  if ! docker inspect "${N8N_CONTAINER}" --format '{{json .NetworkSettings.Networks}}' | grep -q trading-net; then
    echo "=== attach n8n to trading-net ==="
    docker network connect trading-net "${N8N_CONTAINER}" || true
  fi
fi

echo "OpenSearch:  http://ai-trading-opensearch:9200  (n8n / Docker)"
echo "Host:        http://127.0.0.1:9200"
echo "Dashboards:  http://127.0.0.1:5601"
echo "n8n cred:    baseUrl=http://ai-trading-opensearch:9200"
