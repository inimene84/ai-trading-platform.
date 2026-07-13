from vps_ssh_common import ssh_cmd, SSH_BASE
#!/usr/bin/env python3
"""Compare local vs VPS env key lengths and search VPS for env backups."""
import subprocess

REMOTE = r'''
cd /root/ai-trading-platform-v3
echo "=== ENV BACKUPS ==="
ls -la .env* 2>/dev/null || true
find /root -maxdepth 3 -name '.env*' 2>/dev/null | head -20

echo ""
echo "=== KEY LENGTHS IN ALL ENV FILES ==="
for f in .env .env.bak .env.backup .env.prod .env.local; do
  [ -f "$f" ] || continue
  echo "-- $f --"
  for k in TELEGRAM_BOT_TOKEN XAI_API_KEY GOOGLE_API_KEY OPENROUTER_API_KEY KIE_API_KEY TELEGRAM_CHAT_ID; do
    v=$(grep "^${k}=" "$f" 2>/dev/null | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//')
    [ -n "$v" ] && echo "$k len=${#v}" || echo "$k missing"
  done
done

echo ""
echo "=== DOCKER ENV FOR BACKEND (key presence only) ==="
docker inspect ai-trading-backend --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(TELEGRAM|XAI|GOOGLE|OPENROUTER|KIE)_' | sed 's/=.*/=***/'
'''

SSH = ssh_cmd(REMOTE)
r = subprocess.run(SSH, capture_output=True, text=True)
print(r.stdout)
if r.stderr:
    print('STDERR:', r.stderr)
