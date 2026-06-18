#!/usr/bin/env bash
set -euo pipefail
cd "${PROJECT_DIR:-/root/ai-trading-platform-v3}"
SYMS_JSON=$(grep '^TRADING_SYMBOLS=' .env 2>/dev/null | cut -d= -f2- | python3 -c "import sys,json; print(json.dumps([s.strip() for s in sys.stdin.read().split(',') if s.strip()]))" 2>/dev/null || echo '[]')
curl -sf -X POST http://127.0.0.1:8001/trading/loop/start \
  -H 'Content-Type: application/json' \
  -d "{\"interval_minutes\":15,\"strategy\":\"combined\",\"symbols\":${SYMS_JSON}}"
echo
curl -sf http://127.0.0.1:8001/trading/loop/status | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(f\"running={d.get('running')} cycle={d.get('cycle_count')} symbols={len(d.get('symbols',[]))}\")
"
