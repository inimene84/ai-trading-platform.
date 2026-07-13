#!/usr/bin/env python3
"""Deploy LLM/Telegram fixes to Hostinger VPS and verify."""
import subprocess
import sys
from pathlib import Path

from vps_ssh_common import SSH_BASE, scp_cmd

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "/root/ai-trading-platform-v3"
SSH = list(SSH_BASE)

SYNC_ENV = r'''
python3 <<'PY'
import re
from pathlib import Path

TRADING_ENV = Path('/root/ai-trading-platform-v3/.env')
HERMES_ENV = Path('/root/.hermes/.env')

MIN_LEN = {
    'TELEGRAM_BOT_TOKEN': 35,
    'XAI_API_KEY': 20,
    'GOOGLE_API_KEY': 30,
    'ANTHROPIC_API_KEY': 20,
}

def load_env(path: Path) -> dict[str, str]:
    data = {}
    if not path.exists():
        return data
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data

def is_placeholder(key_name: str, value: str) -> bool:
    if not value:
        return True
    min_len = MIN_LEN.get(key_name)
    if min_len and len(value) < min_len:
        return True
    return bool(re.search(r'changeme|placeholder|your_|xxx|dummy|test|fake', value, re.I))

def save_env(path: Path, data: dict[str, str], original_lines: list[str]) -> None:
    keys_written = set()
    out = []
    for line in original_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            out.append(line if line.endswith('\n') else line + '\n')
            continue
        key = stripped.split('=', 1)[0].strip()
        if key in data:
            out.append(f'{key}={data[key]}\n')
            keys_written.add(key)
        else:
            out.append(line if line.endswith('\n') else line + '\n')
    for key, value in data.items():
        if key not in keys_written:
            out.append(f'{key}={value}\n')
    path.write_text(''.join(out))

trading = load_env(TRADING_ENV)
hermes = load_env(HERMES_ENV)
original = TRADING_ENV.read_text().splitlines(keepends=True)

updates = {
    'OPENROUTER_MODEL': 'anthropic/claude-sonnet-5',
    'GEMINI_MODEL': 'google/gemini-2.5-flash',
}
for key_name in ['TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'OPENROUTER_API_KEY', 'KIE_API_KEY']:
    current = trading.get(key_name, '')
    source = hermes.get(key_name, '')
    if source and not is_placeholder(key_name, source) and (
        is_placeholder(key_name, current) or len(source) > len(current)
    ):
        updates[key_name] = source

print('Env updates:', ', '.join(f'{k}(len={len(v)})' for k, v in updates.items()))
trading.update(updates)
save_env(TRADING_ENV, trading, original)
print('Saved .env')
PY
'''

VERIFY = r'''
set -e
cd /root/ai-trading-platform-v3
echo "=== RESTART SERVICES ==="
docker compose -f docker-compose.prod.yml up -d --build backend litellm
sleep 8
docker restart ai-trading-nginx
sleep 3

echo ""
echo "=== HEALTH ==="
curl -sf http://127.0.0.1:8001/health; echo
curl -sf http://127.0.0.1:8081/health; echo

echo ""
echo "=== PROVIDER SPOT CHECK ==="
python3 <<'PY'
import json, os, urllib.request
from pathlib import Path
env={}
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.strip().startswith('#'):
        k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
# Telegram
tg=env.get('TELEGRAM_BOT_TOKEN','')
code,_=0,''
try:
    with urllib.request.urlopen(f'https://api.telegram.org/bot{tg}/getMe', timeout=15) as r:
        print('Telegram getMe:', r.status)
except Exception as e:
    print('Telegram getMe:', getattr(e,'code',e))
# OpenRouter
key=env['OPENROUTER_API_KEY']
payload=json.dumps({'model':'anthropic/claude-sonnet-5','messages':[{'role':'user','content':'Reply JSON only: {\"ok\":true}'}],'max_tokens':20,'response_format':{'type':'json_object'}}).encode()
req=urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=payload, headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print('OpenRouter chat:', r.status)
except Exception as e:
    print('OpenRouter chat:', getattr(e,'code',e))
PY

echo ""
echo "=== MARKET ALERTS DRY RUN ==="
ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)
curl -sf -X POST "http://127.0.0.1:8001/api/news/market-alerts/run?dry_run=true&skip_telegram=true" -H "X-API-Key: $ADMIN" | head -c 400; echo

echo ""
echo "=== RECENT ERRORS (10m) ==="
docker logs ai-trading-backend --since 10m 2>&1 | grep -iE 'CRITICAL|ERROR|Failed to send telegram|All LLM providers' | tail -15 || echo "(none)"
'''


def run(cmd: list[str], input_text: str | None = None) -> subprocess.CompletedProcess:
    print(f"\n$ {' '.join(cmd[:4])} ...")
    return subprocess.run(cmd, input=input_text, capture_output=True, text=True)


def main() -> int:
    files = [
        (ROOT / "backend" / "llm" / "router.py", f"{PROJECT}/backend/llm/router.py"),
        (ROOT / "litellm-config.yaml", f"{PROJECT}/litellm-config.yaml"),
    ]
    for local, remote in files:
        r = run(scp_cmd(str(local), remote))
        if r.returncode != 0:
            print(r.stderr)
            return r.returncode
        print(f"Uploaded {local.name}")

    r = run(SSH + [SYNC_ENV])
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr)
        return r.returncode

    r = run(SSH + [VERIFY])
    print(r.stdout)
    if r.stderr:
        print(r.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
