from vps_ssh_common import ssh_cmd, SSH_BASE
#!/usr/bin/env python3
"""Diagnose Kie truncation, verify new OpenAI/xAI keys, check deployed state."""
import subprocess

REMOTE = r'''
cd /root/ai-trading-platform-v3
echo "=== GIT STATE ==="
git log -1 --oneline
git status -sb | head -15

echo ""
echo "=== ROUTER ON VPS (model ids / providers) ==="
grep -n "OPENROUTER_MODEL\|GEMINI_MODEL\|claude-sonnet-5\|claude-3.5-sonnet\|OPENAI_API_KEY" backend/llm/router.py | head -20

echo ""
echo "=== KEY LENGTHS ==="
for k in OPENAI_API_KEY XAI_API_KEY XAI_MODEL OPENAI_MODEL KIE_API_KEY; do
  v=$(grep "^${k}=" .env 2>/dev/null | cut -d= -f2-)
  echo "$k len=${#v}"
done

echo ""
echo "=== BACKEND CONTAINER ENV (does it have new keys?) ==="
docker exec ai-trading-backend printenv | grep -E '^(OPENAI_API_KEY|XAI_API_KEY)=' | awk -F= '{print $1" len="length($2)}'

echo ""
echo "=== KIE PARSE ERROR COUNT LAST 12H ==="
docker logs ai-trading-backend --since 12h 2>&1 | grep -c "Failed to parse JSON" || true

echo ""
echo "=== KIE STOP REASON TEST (replicating market-alerts call) ==="
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

for tokens in (300, 512, 1024):
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": tokens,
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
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage", {})
        valid = True
        try:
            json.loads(text)
        except Exception:
            valid = False
        print(f"max_tokens={tokens} stop_reason={data.get('stop_reason')} "
              f"out_tokens={usage.get('output_tokens')} json_valid={valid} text_len={len(text)}")
    except Exception as e:
        body = e.read(200).decode() if hasattr(e, 'read') else str(e)
        print(f"max_tokens={tokens} FAILED {getattr(e, 'code', '')} {body}")
PY

echo ""
echo "=== NEW KEY VALIDATION ==="
python3 <<'PY'
import json, urllib.request
from pathlib import Path
env = {}
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.strip().startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

def check(name, url, headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f"{name}: {r.status}")
    except Exception as e:
        body = e.read(150).decode() if hasattr(e, 'read') else str(e)
        print(f"{name}: {getattr(e, 'code', '')} {body}")

oai = env.get('OPENAI_API_KEY', '')
if oai:
    check('OpenAI models', 'https://api.openai.com/v1/models', {'Authorization': f'Bearer {oai}'})
else:
    print('OpenAI: no key in .env')

xai = env.get('XAI_API_KEY', '')
if xai:
    check('xAI models', 'https://api.x.ai/v1/models', {'Authorization': f'Bearer {xai}'})
else:
    print('xAI: no key in .env')
PY
'''

r = subprocess.run(
    ssh_cmd(REMOTE),
    capture_output=True, text=True,
)
print(r.stdout)
if r.stderr:
    print('STDERR:', r.stderr[-800:])
