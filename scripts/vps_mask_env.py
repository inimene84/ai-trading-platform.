#!/usr/bin/env python3
import subprocess
from vps_ssh_common import ssh_cmd, SSH_BASE
REMOTE = r'''
python3 <<'PY'
from pathlib import Path
import re
env = {}
for line in Path('/root/ai-trading-platform-v3/.env').read_text().splitlines():
    if '=' in line and not line.strip().startswith('#'):
        k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
for k in ['TELEGRAM_BOT_TOKEN','XAI_API_KEY','GOOGLE_API_KEY','TELEGRAM_CHAT_ID','KIE_API_KEY','OPENROUTER_API_KEY']:
    v=env.get(k,'')
    masked = '(empty)' if not v else f'len={len(v)} start={v[:3]}... end=...{v[-3:]}' if len(v)>6 else f'len={len(v)} value={v}'
    placeholder = bool(re.search(r'changeme|placeholder|your_|xxx|dummy|test|fake', v, re.I))
    print(f'{k}: {masked} placeholder={placeholder}')
PY
'''
r=subprocess.run(ssh_cmd(REMOTE),capture_output=True,text=True)
print(r.stdout)
