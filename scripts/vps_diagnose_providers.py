#!/usr/bin/env python3
"""Run provider diagnostics on Hostinger VPS via SSH."""
import subprocess
import sys

SSH = [
    "ssh", "-i", r"C:\Users\thori\.ssh\id_vps_bot",
    "-o", "BatchMode=yes", "root@72.60.18.113",
]

REMOTE = r'''
cd /root/ai-trading-platform-v3
python3 <<'PY'
import os, re, json, urllib.request, urllib.error
from pathlib import Path

def load_env():
    d = {}
    for line in Path('.env').read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        d[k.strip()] = v.strip().strip('"').strip("'")
    return d

env = load_env()
print("=== ENV STATUS ===")
for k in ["KIE_API_KEY","OPENROUTER_API_KEY","XAI_API_KEY","GOOGLE_API_KEY",
          "TELEGRAM_BOT_TOKEN","TELEGRAM_CHAT_ID","OPENROUTER_MODEL","GEMINI_MODEL","ANTHROPIC_API_KEY"]:
    v = env.get(k, "")
    if not v:
        print(f"{k}: MISSING")
    elif re.search(r"changeme|placeholder|your_|xxx", v, re.I):
        print(f"{k}: PLACEHOLDER")
    else:
        extra = ""
        if k in ("OPENROUTER_MODEL", "GEMINI_MODEL"):
            extra = f" value={v}"
        print(f"{k}: SET len={len(v)}{extra}")

def http_status(url, headers=None, data=None):
    req = urllib.request.Request(url, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read(200).decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read(200).decode('utf-8', 'replace')
    except Exception as e:
        return 0, str(e)

print("\n=== API TESTS ===")
tg = env.get("TELEGRAM_BOT_TOKEN", "")
code, body = http_status(f"https://api.telegram.org/bot{tg}/getMe")
print(f"Telegram getMe: {code} {body[:120]}")

or_key = env.get("OPENROUTER_API_KEY", "")
code, body = http_status("https://openrouter.ai/api/v1/models", {"Authorization": f"Bearer {or_key}"})
print(f"OpenRouter models: {code} {body[:120]}")

xai = env.get("XAI_API_KEY", "")
code, body = http_status("https://api.x.ai/v1/models", {"Authorization": f"Bearer {xai}"})
print(f"xAI models: {code} {body[:120]}")

gkey = env.get("GOOGLE_API_KEY", "")
code, body = http_status(f"https://generativelanguage.googleapis.com/v1beta/models?key={gkey}")
print(f"Gemini models: {code} {body[:120]}")

kie = env.get("KIE_API_KEY", "")
payload = json.dumps({"model":"claude-sonnet-4-6","max_tokens":30,
    "messages":[{"role":"user","content":"Reply ONLY with JSON: {\"ok\":true}"}]}).encode()
code, body = http_status("https://api.kie.ai/claude/v1/messages",
    {"Authorization": f"Bearer {kie}", "Content-Type": "application/json"}, payload)
print(f"Kie chat: {code} {body[:200]}")

payload = json.dumps({"model":"anthropic/claude-3.5-sonnet","messages":[{"role":"user","content":"Say ok"}],"max_tokens":10}).encode()
code, body = http_status("https://openrouter.ai/api/v1/chat/completions",
    {"Authorization": f"Bearer {or_key}", "Content-Type": "application/json"}, payload)
print(f"OpenRouter chat anthropic/claude-3.5-sonnet: {code} {body[:200]}")

payload = json.dumps({"model":"openrouter/anthropic/claude-3.5-sonnet","messages":[{"role":"user","content":"Say ok"}],"max_tokens":10}).encode()
code, body = http_status("https://openrouter.ai/api/v1/chat/completions",
    {"Authorization": f"Bearer {or_key}", "Content-Type": "application/json"}, payload)
print(f"OpenRouter chat openrouter/anthropic/claude-3.5-sonnet: {code} {body[:200]}")
PY
'''

if __name__ == "__main__":
    r = subprocess.run(SSH + [REMOTE], capture_output=True, text=True)
    print(r.stdout)
    if r.stderr:
        print(r.stderr, file=sys.stderr)
    sys.exit(r.returncode)
