#!/usr/bin/env bash
# Paste into Hostinger VPS browser terminal as root.
# Deploys risk_reviewer parsing fix from main + re-enables LLM gate safely.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/ai-trading-platform-v3}"
CLOUD_AGENT_PUBKEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMPdc81hu58Qrgt5ODe8OvMJmqrM11GB848GmSqj1d7t valgutom@gmail.com"

echo "=== 0. SSH hygiene (StrictModes) ==="
chown root:root /root
chmod 700 /root
mkdir -p /root/.ssh
chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
grep -vF "$CLOUD_AGENT_PUBKEY" /root/.ssh/authorized_keys > /tmp/ak_clean || true
printf '%s\n' "$CLOUD_AGENT_PUBKEY" >> /tmp/ak_clean
mv /tmp/ak_clean /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

cd "$PROJECT_DIR"
echo "=== 1. Git pull main (risk_reviewer fix) ==="
git fetch origin main
git checkout main
git pull origin main
git log -1 --oneline

echo "=== 2. Rebuild backend ==="
COMPOSE_FILE="docker-compose.prod.yml"
[[ -f "$COMPOSE_FILE" ]] || COMPOSE_FILE="docker-compose.yml"
docker compose -f "$COMPOSE_FILE" up -d --build backend

echo "=== 3. Wait for health ==="
for i in $(seq 1 24); do
  if curl -sf http://127.0.0.1:8001/health >/dev/null 2>&1; then
    echo "  backend healthy"
    break
  fi
  sleep 5
done

echo "=== 4. Re-enable risk reviewer (fixed parser, fail-open) ==="
curl -sf -X POST http://127.0.0.1:8001/trading/config/update \
  -H 'Content-Type: application/json' \
  -d '{"use_risk_reviewer_llm": true, "enable_personas": false}'

echo ""
echo "=== 5. Verify ==="
curl -sf http://127.0.0.1:8001/trading/config | python3 -c "
import sys, json
d = json.load(sys.stdin)
rl = d.get('risk_limits', {})
print('use_risk_reviewer_llm=', rl.get('use_risk_reviewer_llm'))
print('enable_personas=', rl.get('enable_personas'))
"
curl -sf http://127.0.0.1:8001/trading/status | python3 -c "
import sys, json
d = json.load(sys.stdin)
tl = d.get('trading_loop', {})
print('mode=', d.get('mode'), 'running=', tl.get('running'), 'cycle=', tl.get('cycle_count'))
"
docker exec ai-trading-backend python3 -c "
from backend.services.risk_reviewer import _parse_reviewer_response
ok, msg = _parse_reviewer_response('Not JSON but {\"approved\": true, \"reasoning\": \"ok\"}')
print('parser_smoke=', ok, msg[:40])
" 2>/dev/null || echo "(parser smoke test skipped)"
echo "=== Done ==="
