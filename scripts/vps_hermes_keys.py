#!/usr/bin/env python3
import subprocess
from vps_ssh_common import ssh_cmd, SSH_BASE
REMOTE = r'''
python3 <<'PY'
from pathlib import Path
for f in ['/root/.hermes/.env']:
    print('===', f, '===')
    env={}
    for line in Path(f).read_text().splitlines():
        if '=' in line and not line.strip().startswith('#'):
            k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
    for k in ['TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID','XAI_API_KEY','GOOGLE_API_KEY','ANTHROPIC_API_KEY']:
        v=env.get(k,'')
        print(f'{k}: len={len(v)}' + (f' start={v[:4]}' if v else ' MISSING'))
PY
'''
r=subprocess.run(ssh_cmd(REMOTE),capture_output=True,text=True)
print(r.stdout)
