#!/usr/bin/env bash
# Paste/run on VPS as root (Hostinger SSH or web terminal) — no Cloud Agent SSH required.
# Fixes nginx /api/backend routing and restarts nginx + rebuilds frontend if repo is present.
set -euo pipefail
PROJECT_DIR="${1:-/root/ai-trading-platform-v3}"
cd "$PROJECT_DIR" || { echo "Missing $PROJECT_DIR"; exit 1; }

git fetch origin 2>/dev/null || true
git checkout cursor/hostinger-vps-fixes-df88 2>/dev/null || git pull origin main 2>/dev/null || true

if [[ -x scripts/hostinger_vps_apply.sh ]]; then
  PROJECT_DIR="$PROJECT_DIR" ./scripts/hostinger_vps_apply.sh
else
  echo "Applying nginx only..."
  docker compose restart nginx
fi

echo "Verify:"
curl -sf "http://127.0.0.1:8081/api/backend/trading/status" | head -c 200
echo ""
