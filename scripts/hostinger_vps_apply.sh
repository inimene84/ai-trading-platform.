#!/usr/bin/env bash
# Run ON the Hostinger VPS (e.g. /root/ai-trading-platform-v3)
# Fixes common production issues: nginx /api/backend routing, Qdrant port, rebuild stack.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/ai-trading-platform-v3}"
cd "$PROJECT_DIR"

echo "=== Hostinger VPS apply ==="
echo "Project: $PROJECT_DIR"

if [[ ! -f docker-compose.yml ]]; then
  echo "ERROR: docker-compose.yml not found in $PROJECT_DIR"
  exit 1
fi

echo ""
echo "=== 1. Git pull ==="
git pull origin main || git pull || true

echo ""
echo "=== 2. Frontend build ==="
cd frontend
npm ci 2>/dev/null || npm install
npm run build
cd ..

echo ""
echo "=== 3. Qdrant port 6333 (if conflict) ==="
if command -v fuser >/dev/null 2>&1; then
  sudo fuser -k 6333/tcp 2>/dev/null || true
fi
docker rm -f vps-qdrant 2>/dev/null || true

echo ""
echo "=== 4. Docker network ==="
docker network create trading-net 2>/dev/null || true
docker network create n8n_default 2>/dev/null || true

echo ""
echo "=== 5. Rebuild & start stack ==="
docker compose down backend nginx 2>/dev/null || true
docker compose up -d --build backend litellm redis qdrant influxdb nginx

echo ""
echo "=== 6. Wait for health ==="
for i in $(seq 1 24); do
  if curl -sf http://127.0.0.1:8001/health >/dev/null 2>&1; then
    echo "Backend healthy on :8001"
    break
  fi
  echo "  waiting backend ($i/24)..."
  sleep 5
done

curl -s http://127.0.0.1:8001/health | head -c 400 || true
echo ""

echo ""
echo "=== 7. Nginx smoke tests ==="
curl -sf -o /dev/null -w "nginx root: %{http_code}\n" http://127.0.0.1:8081/ || true
curl -sf -o /dev/null -w "nginx /api/backend/trading/status: %{http_code}\n" \
  http://127.0.0.1:8081/api/backend/trading/status || true
curl -sf -o /dev/null -w "nginx /api/news/feed: %{http_code}\n" \
  http://127.0.0.1:8081/api/news/feed || true

echo ""
echo "=== 8. Qdrant ==="
curl -s http://127.0.0.1:6333/healthz 2>/dev/null | head -c 200 || echo "Qdrant not reachable on 6333"
echo ""

echo "=== Done. App: http://<vps-ip>:8081/  API: :8001/health ==="
