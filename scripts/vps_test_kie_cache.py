from vps_ssh_common import ssh_cmd, SSH_BASE
#!/usr/bin/env python3
"""Verify Kie proxy accepts Anthropic prompt-caching (system as content blocks)."""
import subprocess

REMOTE = r'''
cd /root/ai-trading-platform-v3
python3 <<'PY'
import json, urllib.request
from pathlib import Path
env = {}
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.strip().startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

key = env['KIE_API_KEY']
# Long-ish static system prompt (cacheable segments usually need >=1024 tokens,
# but the API must at minimum ACCEPT the block format without erroring).
system_blocks = [{
    "type": "text",
    "text": ("You are an expert Crypto Market Sentiment Analyst. Return ONLY raw JSON. "
             "Keys: marketMood, bias, confidence, tradingRecommendation, keyNarratives, topRisks. ") * 20,
    "cache_control": {"type": "ephemeral"},
}]

for run in (1, 2):
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 256,
        "system": system_blocks,
        "messages": [{"role": "user", "content": "F&G is 30. Give brief JSON."}],
    }).encode()
    req = urllib.request.Request(
        "https://api.kie.ai/claude/v1/messages",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "python-httpx/0.27"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
        usage = data.get("usage", {})
        print(f"run{run}: OK stop={data.get('stop_reason')} "
              f"in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
              f"cache_create={usage.get('cache_creation_input_tokens', usage.get('cache_creation'))} "
              f"cache_read={usage.get('cache_read_input_tokens')}")
    except Exception as e:
        body = e.read(300).decode() if hasattr(e, 'read') else str(e)
        print(f"run{run}: FAILED {getattr(e, 'code', '')} {body}")
PY
'''
r = subprocess.run(
    ssh_cmd(REMOTE),
    capture_output=True, text=True, encoding='utf-8', errors='replace',
)
print(r.stdout)
if r.stderr:
    print('STDERR:', r.stderr[-400:])
