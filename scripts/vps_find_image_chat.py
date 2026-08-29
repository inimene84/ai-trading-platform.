import subprocess
import tempfile
from pathlib import Path
from scripts.vps_ssh_common import ssh_cmd, scp_cmd

script = r"""import glob, json

for p in sorted(glob.glob('/var/lib/docker/volumes/agent-zero_a0-data/_data/chats/*/chat.json'), key=lambda x: -json.load(open(x)).get('updated_at', 0) if 'updated_at' in json.load(open(x)) else 0):
    try:
        d = json.load(open(p))
        name = d.get('name', '')
        chat_id = d.get('id', '')
        print(f"=== Chat {chat_id}: {name} ===")
        for agent in d.get('agents', []):
            data = agent.get('data', {})
            ctx = data.get('ctx_window', {})
            text = ctx.get('text', '')
            print(f"Agent {agent.get('number')} context length: {len(text)}")
            if len(text) > 0:
                print("First 300 chars:")
                print(repr(text[:300]))
                print("Last 500 chars:")
                print(repr(text[-500:]))
        print("Last 3 logs:")
        for log in d.get('log', {}).get('logs', [])[-3:]:
            print(" ", log.get('type'), repr(str(log.get('content'))[:100]))
    except Exception as e:
        print(f"Error {p}: {e}")
"""

with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
    f.write(script)
    tmp_path = f.name

try:
    subprocess.run(scp_cmd(tmp_path, "/tmp/find_image_chat.py"), check=True)
    res = subprocess.run(ssh_cmd("python3 /tmp/find_image_chat.py"), capture_output=True, text=True)
    print(res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr)
finally:
    Path(tmp_path).unlink(missing_ok=True)
