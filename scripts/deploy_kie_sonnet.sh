#!/usr/bin/env bash
# Deploy Kie.ai Sonnet 4.6 LiteLLM routing — run ON the VPS as root.
# Usage: cd /root/ai-trading-platform-v3 && ./scripts/deploy_kie_sonnet.sh
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PROJECT_DIR"

echo "=== Deploy Kie Sonnet 4.6 routing ==="
echo "Project: $PROJECT_DIR"

git fetch origin
git checkout main
git pull origin main

echo ""
echo "=== Rebuild backend + litellm ==="
docker compose up -d --build backend litellm

echo ""
echo "=== Wait for backend health ==="
for i in $(seq 1 24); do
  if curl -sf http://127.0.0.1:8001/health >/dev/null 2>&1; then
    echo "Backend healthy"
    break
  fi
  if curl -sf http://127.0.0.1:8081/health >/dev/null 2>&1; then
    echo "Backend healthy (via nginx)"
    break
  fi
  echo "  waiting ($i/24)..."
  sleep 5
done

echo ""
echo "=== Verify LiteLLM ==="
curl -sf http://127.0.0.1:4001/health | head -c 300 || echo "LiteLLM health check failed"
echo ""

echo ""
echo "=== Verify AI routing ==="
curl -sf http://127.0.0.1:8081/api/backend/trading/models | python3 -c "
import sys, json
d = json.load(sys.stdin)
dm = d.get('decision_model', {})
print('decision_model:', dm.get('model'), dm.get('provider'), 'configured=', dm.get('configured'))
fc = d.get('fallback_chain', [{}])[0]
print('first_fallback:', fc.get('provider'), fc.get('model'), 'configured=', fc.get('configured'))
" 2>/dev/null || curl -sf http://127.0.0.1:8081/api/backend/trading/models | head -c 500

echo ""
echo "=== Done ==="
