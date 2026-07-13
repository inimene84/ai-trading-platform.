#!/usr/bin/env python3
"""Inspect raw Kie response structure to find why JSON is invalid."""
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
system = ("You are an expert Crypto Market Sentiment Analyst. Return ONLY raw JSON. "
          "Keys: marketMood, bias, confidence, tradingRecommendation, keyNarratives (array), topRisks (array).")
prompt = ("Fear & Greed: 28 (Fear). Headlines: BTC tests 62K support amid Hormuz tensions; "
          "ETH consolidates; SOL ETF inflows. Analyze and give JSON.")

payload = json.dumps({
    "model": "claude-sonnet-4-6",
    "max_tokens": 1024,
    "system": system,
    "messages": [{"role": "user", "content": prompt}],
}).encode()
req = urllib.request.Request(
    "https://api.kie.ai/claude/v1/messages",
    data=payload,
    headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "python-httpx/0.27",
    },
)
with urllib.request.urlopen(req, timeout=60) as r:
    data = json.load(r)

print("content blocks:", [(b.get("type"), len(b.get("text",""))) for b in data.get("content", [])])
text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
print("REPR FIRST 300:")
print(repr(text[:300]))
print("REPR LAST 200:")
print(repr(text[-200:]))
print("stop_reason:", data.get("stop_reason"))
print("usage:", data.get("usage"))

# count braces
print("open braces:", text.count("{"), "close braces:", text.count("}"))
PY
'''

r = subprocess.run(
    ['ssh', '-i', r'C:\Users\thori\.ssh\id_vps_bot', '-o', 'BatchMode=yes', 'root@72.60.18.113', REMOTE],
    capture_output=True, text=True,
)
print(r.stdout)
if r.stderr:
    print('STDERR:', r.stderr[-500:])
