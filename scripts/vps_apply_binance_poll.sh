#!/usr/bin/env bash
set -euo pipefail
cd /root/ai-trading-platform-v3

for key in BINANCE_WALLET_POLL_INTERVAL BINANCE_ORDER_POLL_INTERVAL; do
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=180|" .env
  else
    echo "${key}=180" >> .env
  fi
done

echo "=== Updated .env ==="
grep -E "^BINANCE_(WALLET|ORDER)_POLL_INTERVAL=" .env

echo "=== Restarting backend ==="
docker compose -f docker-compose.prod.yml restart backend
sleep 15

echo "=== Health ==="
curl -sf http://127.0.0.1:8001/health
echo

echo "=== Binance ==="
python3 /tmp/vps_status_parse.py 2>/dev/null | sed -n '/=== BINANCE ===/,$p' | head -10
