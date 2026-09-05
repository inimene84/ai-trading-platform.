import subprocess
import tempfile
from pathlib import Path
from scripts.vps_ssh_common import ssh_cmd, scp_cmd

test_script = """import sys
import requests
import litellm
import os

kie_key = '${KIE_API_KEY}'
omni_key = os.environ.get('OMNIROUTE_API_KEY', '')

def log(msg):
    print(msg, flush=True)

log('--- Test 1: kie.ai direct messages with x-api-key ---')
try:
    r = requests.post('https://api.kie.ai/claude/v1/messages', 
                      headers={'x-api-key': kie_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                      json={'model': 'claude-sonnet-4-6', 'max_tokens': 20, 'messages': [{'role': 'user', 'content': 'hi'}]},
                      timeout=5)
    log(f'Status: {r.status_code} {r.text[:200]}')
except Exception as e:
    log(f'Err: {e}')

log('--- Test 2: kie.ai direct messages with Bearer token ---')
try:
    r = requests.post('https://api.kie.ai/claude/v1/messages', 
                      headers={'Authorization': f'Bearer {kie_key}', 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                      json={'model': 'claude-sonnet-4-6', 'max_tokens': 20, 'messages': [{'role': 'user', 'content': 'hi'}]},
                      timeout=5)
    log(f'Status: {r.status_code} {r.text[:200]}')
except Exception as e:
    log(f'Err: {e}')

log('--- Test 3: kieai-proxy (http://kieai-proxy:11434/v1) ---')
try:
    r = requests.post('http://kieai-proxy:11434/v1/chat/completions', 
                      headers={'Authorization': f'Bearer {kie_key}', 'content-type': 'application/json'},
                      json={'model': 'claude-sonnet-4-6', 'max_tokens': 20, 'messages': [{'role': 'user', 'content': 'hi'}]},
                      timeout=5)
    log(f'Status: {r.status_code} {r.text[:200]}')
except Exception as e:
    log(f'Err: {e}')

log('--- Test 4: omniroute (https://omni.allikas.online/v1) ---')
try:
    r = requests.post('https://omni.allikas.online/v1/chat/completions', 
                      headers={'Authorization': f'Bearer {omni_key}', 'content-type': 'application/json'},
                      json={'model': 'auto/chat', 'max_tokens': 20, 'messages': [{'role': 'user', 'content': 'hi'}]},
                      timeout=5)
    log(f'Status: {r.status_code} {r.text[:200]}')
except Exception as e:
    log(f'Err: {e}')

log('--- Test 5: LiteLLM call anthropic claude via kie.ai ---')
try:
    res = litellm.completion(
        model="anthropic/claude-sonnet-4-6",
        messages=[{"role": "user", "content": "hi"}],
        api_base="https://api.kie.ai/claude",
        api_key=kie_key,
        extra_headers={"Authorization": f"Bearer {kie_key}"},
        max_tokens=20,
        timeout=10
    )
    log(f'LiteLLM Anthropic Kie Response: {res.choices[0].message.content}')
except Exception as e:
    log(f'LiteLLM Anthropic Kie Err: {type(e)} {e}')

log('--- Test 6: LiteLLM call openai claude via kieai-proxy ---')
try:
    res = litellm.completion(
        model="openai/claude-sonnet-4-6",
        messages=[{"role": "user", "content": "hi"}],
        api_base="http://kieai-proxy:11434/v1",
        api_key=kie_key,
        max_tokens=20,
        timeout=10
    )
    log(f'LiteLLM Proxy Response: {res.choices[0].message.content}')
except Exception as e:
    log(f'LiteLLM Proxy Err: {type(e)} {e}')

log('--- Test 7: LiteLLM call omniroute ---')
try:
    res = litellm.completion(
        model="openai/auto/chat",
        messages=[{"role": "user", "content": "hi"}],
        api_base="https://omni.allikas.online/v1",
        api_key=omni_key,
        max_tokens=20,
        timeout=10
    )
    log(f'LiteLLM OmniRoute Response: {res.choices[0].message.content}')
except Exception as e:
    log(f'LiteLLM OmniRoute Err: {type(e)} {e}')
"""

with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
    f.write(test_script)
    local_tmp = f.name

try:
    subprocess.run(scp_cmd(local_tmp, "/tmp/test_agent0_llms.py"), check=True)
    subprocess.run(ssh_cmd("docker cp /tmp/test_agent0_llms.py a0-instance:/tmp/test_agent0_llms.py"), check=True)
    res = subprocess.run(ssh_cmd("docker exec a0-instance /opt/venv-a0/bin/python /tmp/test_agent0_llms.py"), capture_output=True, text=True)
    print("OUTPUT:")
    print(res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr)
finally:
    Path(local_tmp).unlink(missing_ok=True)


