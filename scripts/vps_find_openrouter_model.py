#!/usr/bin/env python3
"""Find working OpenRouter models on VPS."""
import subprocess
import sys

REMOTE = r'''
python3 <<'PY'
import json, urllib.request
from pathlib import Path

env = {}
for line in Path('/root/ai-trading-platform-v3/.env').read_text().splitlines():
    if '=' in line and not line.strip().startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

key = env['OPENROUTER_API_KEY']
req = urllib.request.Request(
    'https://openrouter.ai/api/v1/models',
    headers={'Authorization': f'Bearer {key}'},
)
data = json.load(urllib.request.urlopen(req, timeout=30))
models = [m['id'] for m in data.get('data', []) if any(
    x in m['id'].lower() for x in ('claude', 'sonnet', 'gpt-4o', 'gemini')
)]
print('Candidate models:', models[:20])

for test in models[:8] + [
    'anthropic/claude-sonnet-4',
    'anthropic/claude-3.7-sonnet',
    'openai/gpt-4o',
    'openai/gpt-4o-mini',
    'google/gemini-2.5-flash',
]:
    payload = json.dumps({
        'model': test,
        'messages': [{'role': 'user', 'content': 'Say ok'}],
        'max_tokens': 5,
    }).encode()
    req = urllib.request.Request(
        'https://openrouter.ai/api/v1/chat/completions',
        data=payload,
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f'OK {test} -> {r.status}')
    except Exception as e:
        print(f'FAIL {test} -> {getattr(e, "code", e)}')
PY
'''

SSH = [
    'ssh', '-i', r'C:\Users\thori\.ssh\id_vps_bot',
    '-o', 'BatchMode=yes', 'root@72.60.18.113', REMOTE,
]

if __name__ == '__main__':
    r = subprocess.run(SSH, capture_output=True, text=True)
    print(r.stdout)
    if r.stderr:
        print(r.stderr, file=sys.stderr)
    sys.exit(r.returncode)
