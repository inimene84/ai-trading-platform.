import subprocess
import tempfile
from pathlib import Path
from scripts.vps_ssh_common import ssh_cmd, scp_cmd

test_code = r"""import os, sys, json
sys.path.insert(0, '/a0')
from agent import Agent, AgentContext
from plugins._model_config.helpers.model_config import resolve_preset, get_effective_config, build_chat_model

print('Resolve OmniRoute Auto:', resolve_preset('OmniRoute Auto'))
print('Resolve OmniRoute Claude Sonnet:', resolve_preset('OmniRoute Claude Sonnet'))
print('Resolve Kie.ai Sonnet:', resolve_preset('Kie.ai Sonnet'))

ctx = AgentContext(config={})
ctx.set_data('chat_model_override', {'preset_name': 'OmniRoute Claude Sonnet'})
agent = Agent(0, {}, context=ctx)
eff = get_effective_config(agent)
print('Effective config with override:')
print('chat_model:', eff.get('chat_model'))
print('utility_model:', eff.get('utility_model'))

m = build_chat_model(agent)
print('Built chat model:', m.model_name, m.provider, m.kwargs)
"""

with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
    f.write(test_code)
    p = f.name

try:
    subprocess.run(scp_cmd(p, "/tmp/test_resolve.py"), check=True)
    subprocess.run(ssh_cmd("docker cp /tmp/test_resolve.py a0-instance:/tmp/test_resolve.py"), check=True)
    res = subprocess.run(ssh_cmd("docker exec a0-instance /opt/venv-a0/bin/python /tmp/test_resolve.py"), capture_output=True, text=True)
    print("OUTPUT:")
    print(res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr)
finally:
    Path(p).unlink(missing_ok=True)
