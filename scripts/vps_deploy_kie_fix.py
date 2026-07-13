#!/usr/bin/env python3
"""Deploy Kie JSON fixes to VPS, rebuild backend, verify end-to-end."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSH_KEY = str(Path.home() / ".ssh" / "id_vps_bot")
HOST = "root@72.60.18.113"
PROJECT = "/root/ai-trading-platform-v3"

PREFILL_TEST = r'''
cd /root/ai-trading-platform-v3
echo "=== KIE PREFILL SANITY TEST ==="
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
prompt = ("Fear & Greed: 28 (Fear). Headlines: BTC tests 62K support amid Hormuz tensions. Analyze and give JSON.")

payload = json.dumps({
    "model": "claude-sonnet-4-6",
    "max_tokens": 1024,
    "system": system,
    "messages": [
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": "{"},
    ],
}).encode()
req = urllib.request.Request(
    "https://api.kie.ai/claude/v1/messages",
    data=payload,
    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
             "User-Agent": "python-httpx/0.27"},
)
with urllib.request.urlopen(req, timeout=60) as r:
    data = json.load(r)
text = "{" + "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
try:
    parsed = json.loads(text)
    print("PREFILL JSON VALID: True, keys:", sorted(parsed.keys()))
except Exception as e:
    print("PREFILL JSON VALID: False:", e)
    print(repr(text[:200]))
print("stop_reason:", data.get("stop_reason"), "out_tokens:", data.get("usage", {}).get("output_tokens"))
PY
'''

DEPLOY = r'''
set -e
cd /root/ai-trading-platform-v3
docker compose -f docker-compose.prod.yml up -d --build backend
for i in $(seq 1 15); do
  curl -sf http://127.0.0.1:8001/health >/dev/null && break
  sleep 5
done
ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)
curl -sf -X POST http://127.0.0.1:8001/sentry/resume -H "X-API-Key: $ADMIN" -H 'Content-Type: application/json' -d '{"note":"kie fix deploy"}' >/dev/null || true
sleep 2

echo "=== MARKET ALERTS DRY RUN x3 (verify Kie primary succeeds) ==="
for i in 1 2 3; do
  curl -sf -X POST "http://127.0.0.1:8001/api/news/market-alerts/run?dry_run=true&skip_telegram=true" -H "X-API-Key: $ADMIN" | python3 -c "import sys,json; d=json.load(sys.stdin)['summary']; print(f\"run: source={d['source']} bias={d['bias']} confidence={d['confidence']}\")"
done

echo ""
echo "=== ROUTER LOG (which provider won) ==="
docker logs ai-trading-backend --since 5m 2>&1 | grep -E "LLM Router: (Success|Trying)" | tail -8

echo ""
echo "=== PARSE ERRORS SINCE RESTART ==="
docker logs ai-trading-backend --since 5m 2>&1 | grep -c "Failed to parse JSON" || true
'''


def run(cmd, label):
    print(f"--- {label} ---")
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout)
    if r.returncode != 0:
        print("STDERR:", r.stderr[-1500:])
    return r.returncode


def main():
    rc = run(["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", HOST, PREFILL_TEST], "prefill test")
    if rc != 0:
        return rc
    for local, remote in [
        (ROOT / "backend" / "llm" / "router.py", f"{PROJECT}/backend/llm/router.py"),
        (ROOT / "backend" / "services" / "market_alerts.py", f"{PROJECT}/backend/services/market_alerts.py"),
    ]:
        rc = run(["scp", "-i", SSH_KEY, "-o", "BatchMode=yes", str(local), f"{HOST}:{remote}"], f"upload {local.name}")
        if rc != 0:
            return rc
    return run(["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", HOST, DEPLOY], "deploy + verify")


if __name__ == "__main__":
    sys.exit(main())
