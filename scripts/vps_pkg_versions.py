#!/usr/bin/env python3
"""Check package versions inside VPS backend container."""
import subprocess
import sys

from vps_ssh_common import ssh_cmd

REMOTE = r"""
docker exec ai-trading-backend python3 -c "
import importlib
mods = ['urllib3','requests','idna','starlette','cryptography','aiohttp','fastapi']
for m in mods:
    try:
        mod = importlib.import_module(m)
        print(f'{m}={getattr(mod,\"__version__\",\"?\")}')
    except Exception as e:
        print(f'{m}=MISSING ({e})')
"
"""

if __name__ == "__main__":
    r = subprocess.run(ssh_cmd(REMOTE), capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout)
    if r.stderr:
        print(r.stderr[-500:], file=sys.stderr)
    sys.exit(r.returncode)
