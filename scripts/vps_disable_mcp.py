import subprocess
import tempfile
from pathlib import Path
from scripts.vps_ssh_common import ssh_cmd, scp_cmd

script = """import json
p = '/var/lib/docker/volumes/agent-zero_a0-data/_data/settings.json'
with open(p, 'r', encoding='utf-8') as f:
    d = json.load(f)

# Disable excessive MCP servers that flood the prompt with 372 tools
if 'mcp_servers' in d:
    s = d['mcp_servers']
    s = s.replace("'disabled': False", "'disabled': True")
    d['mcp_servers'] = s

with open(p, 'w', encoding='utf-8') as f:
    json.dump(d, f, indent=4)
print('Disabled bloated MCP servers in settings.json successfully')
"""

with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
    f.write(script)
    tmp_path = f.name

try:
    subprocess.run(scp_cmd(tmp_path, "/tmp/disable_mcp.py"), check=True)
    res = subprocess.run(ssh_cmd("python3 /tmp/disable_mcp.py"), capture_output=True, text=True)
    print(res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr)
finally:
    Path(tmp_path).unlink(missing_ok=True)
