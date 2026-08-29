import subprocess
import tempfile
from pathlib import Path
from scripts.vps_ssh_common import ssh_cmd, scp_cmd

code = r"""import os, sys, asyncio
sys.path.insert(0, '/a0')
import models

async def test():
    print('Testing kieai-claude...')
    m1 = models.get_chat_model('kieai-claude', 'claude-sonnet-4-6')
    r1 = await m1.unified_call([{'role': 'user', 'content': 'Say Claude OK'}])
    print('Claude response:', r1[0])

    print('Testing kieai-gpt-codex...')
    m2 = models.get_chat_model('kieai-gpt-codex', 'gpt-5.4-codex')
    r2 = await m2.unified_call([{'role': 'user', 'content': 'Say Codex OK'}])
    print('Codex response:', r2[0])

asyncio.run(test())
"""

with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
    f.write(code)
    p = f.name

try:
    subprocess.run(scp_cmd(p, "/tmp/test_both.py"), check=True)
    subprocess.run(ssh_cmd("docker cp /tmp/test_both.py a0-instance:/tmp/test_both.py"), check=True)
    res = subprocess.run(ssh_cmd("docker exec a0-instance /opt/venv-a0/bin/python /tmp/test_both.py"), capture_output=True, text=True)
    print("OUTPUT:")
    print(res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr)
finally:
    Path(p).unlink(missing_ok=True)
