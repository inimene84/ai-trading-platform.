import subprocess
import tempfile
from pathlib import Path
from scripts.vps_ssh_common import ssh_cmd, scp_cmd

test_code = """import os, sys
sys.path.insert(0, '/a0')
import models
from helpers.providers import get_provider_config

import os, sys
sys.path.insert(0, '/a0')
import models
from helpers.providers import get_provider_config

import asyncio

async def test():
    print('--- Test 1: kieai-gpt-codex with gpt-5.4-codex ---', flush=True)
    try:
        m1 = models.get_chat_model("kieai-gpt-codex", "gpt-5.4-codex")
        msg = [{"role": "user", "content": "Say hello in 3 words"}]
        resp1 = await m1.unified_call(msg)
        print("Response 1:", resp1, flush=True)
    except Exception as e:
        print("Error 1:", type(e), e, flush=True)

    print('--- Test 2: kieai-claude with claude-sonnet-4-6 via proxy ---', flush=True)
    try:
        m2 = models.get_chat_model("openai", "claude-sonnet-4-6", api_base="http://kieai-proxy:11434/v1", api_key=os.environ.get('KIE_API_KEY', ''))
        msg = [{"role": "user", "content": "Say hello in 3 words"}]
        resp2 = await m2.unified_call(msg)
        print("Response 2:", resp2, flush=True)
    except Exception as e:
        print("Error 2:", type(e), e, flush=True)

asyncio.run(test())




"""

with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
    f.write(test_code)
    tmp_path = f.name

try:
    subprocess.run(scp_cmd(tmp_path, "/tmp/test_a0_internals.py"), check=True)
    subprocess.run(ssh_cmd("docker cp /tmp/test_a0_internals.py a0-instance:/tmp/test_a0_internals.py"), check=True)
    res = subprocess.run(ssh_cmd("docker exec a0-instance /opt/venv-a0/bin/python /tmp/test_a0_internals.py"), capture_output=True, text=True)
    print("OUTPUT:")
    print(res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr)
finally:
    Path(tmp_path).unlink(missing_ok=True)
