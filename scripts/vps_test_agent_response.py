import subprocess
import tempfile
from pathlib import Path
from scripts.vps_ssh_common import ssh_cmd, scp_cmd

test_code = r"""import os, sys, json, asyncio
sys.path.insert(0, '/a0')
from agent import Agent

agent = Agent(0, {})
chat_model = agent.get_chat_model()
print('Chat model:', chat_model.model_name, chat_model.provider, chat_model.kwargs)

async def test():
    sys_prompt = 'You are Agent Zero. Format response as JSON:\n{"thoughts": ["greet user"], "headline": "Greeting", "tool_name": "response", "tool_args": {"text": "Hello! How can I help?"}}'
    res = await chat_model.unified_call([
        {'role': 'system', 'content': sys_prompt},
        {'role': 'user', 'content': 'Hello'}
    ])
    print('Raw response:', repr(res))

asyncio.run(test())
"""

with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
    f.write(test_code)
    p = f.name

try:
    subprocess.run(scp_cmd(p, "/tmp/test_agent_response.py"), check=True)
    subprocess.run(ssh_cmd("docker cp /tmp/test_agent_response.py a0-instance:/tmp/test_agent_response.py"), check=True)
    res = subprocess.run(ssh_cmd("docker exec a0-instance /opt/venv-a0/bin/python /tmp/test_agent_response.py"), capture_output=True, text=True)
    print("OUTPUT:")
    print(res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr)
finally:
    Path(p).unlink(missing_ok=True)
