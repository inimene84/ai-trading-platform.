#!/usr/bin/env python3
"""Sync valid API keys from hermes .env to trading platform .env on VPS."""
import subprocess
import sys

REMOTE = r'''
python3 <<'PY'
import re
from pathlib import Path

TRADING_ENV = Path('/root/ai-trading-platform-v3/.env')
HERMES_ENV = Path('/root/.hermes/.env')

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

def save_env(path: Path, data: dict[str, str], original_lines: list[str]) -> None:
    keys_written = set()
    out = []
    for line in original_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            out.append(line)
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

MIN_LEN = {
    'TELEGRAM_BOT_TOKEN': 35,
    'XAI_API_KEY': 20,
    'GOOGLE_API_KEY': 30,
    'ANTHROPIC_API_KEY': 20,
}

def is_placeholder(key_name: str, value: str) -> bool:
    if not value:
        return True
    min_len = MIN_LEN.get(key_name)
    if min_len and len(value) < min_len:
        return True
    return bool(re.search(r'changeme|placeholder|your_|xxx|dummy|test|fake', value, re.I))

trading = load_env(TRADING_ENV)
hermes = load_env(HERMES_ENV)
original = TRADING_ENV.read_text().splitlines(keepends=True)

sync_keys = [
    'TELEGRAM_BOT_TOKEN',
    'TELEGRAM_CHAT_ID',
    'XAI_API_KEY',
    'GOOGLE_API_KEY',
    'ANTHROPIC_API_KEY',
    'OPENROUTER_API_KEY',
    'KIE_API_KEY',
]

updates = {}
print('=== SYNC PLAN ===')
for key_name in sync_keys:
    current = trading.get(key_name, '')
    source = hermes.get(key_name, '')
    current_bad = is_placeholder(key_name, current)
    source_ok = source and not is_placeholder(key_name, source)
    if source_ok and (current_bad or len(source) > len(current)):
        updates[key_name] = source
        print(f'UPDATE {key_name}: len {len(current)} -> {len(source)}')
    else:
        print(f'KEEP {key_name}: current_len={len(current)} hermes_len={len(source)}')

# Always set working OpenRouter model defaults
updates['OPENROUTER_MODEL'] = 'anthropic/claude-sonnet-5'
updates['GEMINI_MODEL'] = 'google/gemini-2.5-flash'
print('SET OPENROUTER_MODEL=anthropic/claude-sonnet-5')
print('SET GEMINI_MODEL=google/gemini-2.5-flash')

trading.update(updates)
save_env(TRADING_ENV, trading, original)
print('Saved .env')
PY
'''

SSH = ['ssh', '-i', r'C:\Users\thori\.ssh\id_vps_bot', '-o', 'BatchMode=yes', 'root@72.60.18.113', REMOTE]
if __name__ == '__main__':
    r = subprocess.run(SSH, capture_output=True, text=True)
    print(r.stdout)
    if r.stderr:
        print(r.stderr, file=sys.stderr)
    sys.exit(r.returncode)
