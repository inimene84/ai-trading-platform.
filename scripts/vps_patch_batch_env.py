#!/usr/bin/env python3
"""Set rate-limit batching env vars on VPS .env."""
import subprocess
import sys

from vps_ssh_common import ssh_cmd

ENV_PATCH = r"""
cd /root/ai-trading-platform-v3
python3 << 'PY'
from pathlib import Path
updates = {
    "SYMBOL_BATCH_SIZE": "5",
    "SYMBOL_BATCH_PAUSE_SEC": "3",
    "SYMBOL_CONCURRENCY": "2",
    "TICKER_CACHE_TTL_SEC": "15",
    "BINANCE_WALLET_POLL_INTERVAL": "180",
    "BINANCE_ORDER_POLL_INTERVAL": "180",
}
path = Path(".env")
lines = path.read_text().splitlines() if path.exists() else []
keys = set(updates)
out = []
seen = set()
for line in lines:
    if "=" in line and not line.strip().startswith("#"):
        k = line.split("=", 1)[0].strip()
        if k in updates:
            out.append(f"{k}={updates[k]}")
            seen.add(k)
            continue
    out.append(line)
for k, v in updates.items():
    if k not in seen:
        out.append(f"{k}={v}")
path.write_text("\n".join(out) + "\n")
for k in updates:
    print(next(l for l in out if l.startswith(k + "=")))
PY
"""


def main() -> int:
    r = subprocess.run(
        ssh_cmd(ENV_PATCH),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(r.stdout)
    if r.stderr:
        print("STDERR:", r.stderr[-800:], file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
