from vps_ssh_common import ssh_cmd, SSH_BASE
#!/usr/bin/env python3
"""Update VPS .env: 20 scan coins, 10 max positions, equity-based sizing."""
import subprocess
import sys

REMOTE = r'''
cd /root/ai-trading-platform-v3
python3 <<'PY'
from pathlib import Path

p = Path('.env')
lines = p.read_text().splitlines(keepends=True)

# 20 liquid USDT perps — all present in the service's static LOT_SIZE/tick maps.
updates = {
    # ADA/ARB/DOGE/APT excluded — Jul-2026 weekly loss leaders.
    'TRADING_SYMBOLS': ('ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT,'
                        'AVAXUSDT,LINKUSDT,NEARUSDT,LTCUSDT,DOTUSDT,ATOMUSDT,'
                        'OPUSDT,INJUSDT,SUIUSDT,UNIUSDT,POLUSDT,BTCUSDT'),
    'MAX_POSITIONS': '10',
    'MAX_SAME_DIRECTION_POSITIONS': '3',
    'EQUITY_SIZING_ENABLED': 'true',
    'RISK_PER_TRADE_PCT': '0.01',
    'MAX_TRADE_NOTIONAL_EQUITY_MULT': '1.0',
    'SYMBOL_CONCURRENCY': '4',
}

seen = set()
out = []
for line in lines:
    stripped = line.strip()
    key = stripped.split('=', 1)[0] if '=' in stripped and not stripped.startswith('#') else None
    if key in updates:
        out.append(f'{key}={updates[key]}\n')
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f'{key}={value}\n')

p.write_text(''.join(out))
for key in updates:
    print(f'{key} set')
PY
'''

r = subprocess.run(
    ssh_cmd(REMOTE),
    capture_output=True, text=True, encoding='utf-8', errors='replace',
)
print(r.stdout)
if r.stderr:
    print('STDERR:', r.stderr[-400:])
sys.exit(r.returncode)
