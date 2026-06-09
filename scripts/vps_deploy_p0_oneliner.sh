#!/usr/bin/env bash
# Paste into Hostinger VPS browser terminal as root.
# Deploys P0 safety gates from cursor/observability-env-vars-f7d8.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/ai-trading-platform-v3}"
BRANCH="cursor/observability-env-vars-f7d8"
CLOUD_AGENT_PUBKEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMPdc81hu58Qrgt5ODe8OvMJmqrM11GB848GmSqj1d7t valgutom@gmail.com"

echo "=== 0. SSH + authorized_keys hygiene ==="
# sshd rejects pubkey auth if /root is not owned by root (StrictModes)
chown root:root /root
chmod 700 /root
mkdir -p /root/.ssh
chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
# De-dupe cloud-agent key; keep hostinger-managed rsa on its own line
grep -vF "$CLOUD_AGENT_PUBKEY" /root/.ssh/authorized_keys > /tmp/ak_clean || true
if grep -q '#hostinger-managed-key' /tmp/ak_clean 2>/dev/null; then
  sed -i 's/#hostinger-managed-key.*/#hostinger-managed-key/' /tmp/ak_clean
fi
printf '%s\n' "$CLOUD_AGENT_PUBKEY" >> /tmp/ak_clean
mv /tmp/ak_clean /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
echo "  /root owner: $(stat -c '%U:%G' /root)"

cd "$PROJECT_DIR"
echo "=== 1. Git: fetch + checkout $BRANCH ==="
git fetch origin "$BRANCH"
git stash push -m "pre-p0-deploy-$(date +%s)" 2>/dev/null || true
git checkout "$BRANCH"
git pull origin "$BRANCH"

echo "=== 2. Env fixes ==="
if [[ -f .env ]]; then
  # Exact line replace (avoid prefix-match bug on 0.005)
  sed -i 's/^PYRAMID_MIN_IMPROVEMENT=.*/PYRAMID_MIN_IMPROVEMENT=0/' .env
  grep -q '^MIN_AVAILABLE_MARGIN_USDT=' .env || echo 'MIN_AVAILABLE_MARGIN_USDT=5' >> .env
  grep -q '^MAX_SAME_DIRECTION_POSITIONS=' .env || echo 'MAX_SAME_DIRECTION_POSITIONS=5' >> .env
  grep '^PYRAMID_MIN_IMPROVEMENT=' .env
fi

echo "=== 3. Rebuild backend ==="
COMPOSE_FILE="docker-compose.prod.yml"
[[ -f "$COMPOSE_FILE" ]] || COMPOSE_FILE="docker-compose.yml"
docker compose -f "$COMPOSE_FILE" build backend
docker compose -f "$COMPOSE_FILE" up -d backend

echo "=== 4. Verify ==="
sleep 5
curl -sf http://127.0.0.1:8001/trading/status | python3 -c "import sys,json; d=json.load(sys.stdin); print('backend:', d.get('backend'), 'cycle:', d.get('trading_loop',{}).get('cycle_count'))"
docker logs ai-trading-backend --tail 20 2>&1 | grep -E 'MARGIN GATE|started|error' || docker logs ai-trading-backend --tail 10
echo "=== Done ==="
