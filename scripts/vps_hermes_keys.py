#!/usr/bin/env python3
import subprocess
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
r=subprocess.run(['ssh','-i',r'C:\Users\thori\.ssh\id_vps_bot','-o','BatchMode=yes','root@72.60.18.113',REMOTE],capture_output=True,text=True)
print(r.stdout)
