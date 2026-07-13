#!/usr/bin/env python3
import subprocess
REMOTE = r'''
for f in /root/browser-harness/.env /root/.hermes/.env /root/.agent-zero/.env /root/space-agent/.env; do
  [ -f "$f" ] || continue
  echo "=== $f ==="
  for k in TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID XAI_API_KEY GOOGLE_API_KEY OPENROUTER_API_KEY KIE_API_KEY ANTHROPIC_API_KEY; do
    v=$(grep "^${k}=" "$f" 2>/dev/null | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//')
    [ -n "$v" ] && echo "$k len=${#v}" || true
  done
done
'''
r = subprocess.run(['ssh','-i',r'C:\Users\thori\.ssh\id_vps_bot','-o','BatchMode=yes','root@72.60.18.113',REMOTE], capture_output=True, text=True)
print(r.stdout)
