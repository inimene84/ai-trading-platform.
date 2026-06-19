#!/usr/bin/env bash
set -euo pipefail
cd /root/ai-trading-platform-v3
docker cp backend/services/binance_futures_service.py ai-trading-backend:/app/backend/services/binance_futures_service.py
docker cp backend/services/trading_loop.py ai-trading-backend:/app/backend/services/trading_loop.py
docker restart ai-trading-backend
echo "Waiting for backend..."
sleep 15
docker exec ai-trading-backend curl -sf http://127.0.0.1:8000/health
echo
docker cp scripts/vps_restore_protection.py ai-trading-backend:/tmp/vps_restore_protection.py
docker exec -e PYTHONPATH=/app ai-trading-backend python3 /tmp/vps_restore_protection.py
bash scripts/vps_check_protection.sh
