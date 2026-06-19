#!/usr/bin/env bash
set -euo pipefail
cd /root/ai-trading-platform-v3
docker cp backend/services/binance_futures_service.py ai-trading-backend:/app/backend/services/binance_futures_service.py
docker cp backend/services/trading_loop.py ai-trading-backend:/app/backend/services/trading_loop.py
docker restart ai-trading-backend
echo "Waiting for backend..."
sleep 12
docker exec ai-trading-backend curl -sf http://127.0.0.1:8000/health
echo
docker exec -e PYTHONPATH=/app ai-trading-backend python3 - <<'PY'
import sys
sys.path.insert(0, "/app")
from backend.database.connection import SessionLocal
from backend.database.models import Trade
from backend.services.binance_futures_service import binance_futures_broker

db = SessionLocal()
try:
    trades = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
    for t in trades:
        if not t.stop_loss and not t.take_profit:
            print(f"SKIP {t.symbol}: no SL/TP in DB")
            continue
        res = binance_futures_broker.ensure_protective_orders(
            t.symbol, t.direction, t.stop_loss, t.take_profit,
        )
        print(f"{t.symbol} {t.direction}: {res}")
finally:
    db.close()
PY
bash /root/ai-trading-platform-v3/scripts/vps_check_protection.sh
