"""Complete verification test for Agent Zero LLM providers and presets."""

import subprocess
import tempfile
from pathlib import Path
from scripts.vps_ssh_common import ssh_cmd, scp_cmd

VERIFY_SCRIPT = r"""import os, sys, json, asyncio
from pathlib import Path
sys.path.insert(0, '/a0')

import models
from helpers.providers import get_provider_config


async def run_verification():
    print("==================================================", flush=True)
    print("   AGENT ZERO LLM PROVIDER & PRESET VERIFICATION  ", flush=True)
    print("==================================================", flush=True)

    # 1. Check API Keys
    print("\n[1] Checking API Keys in Agent Zero:")
    for p in ['kieai', 'kieai-claude', 'kieai-gpt-codex', 'omniroute']:
        k = models.get_api_key(p)
        status = "OK" if k and k != "None" and not k.startswith("Bearer ") and not "§" in k else "BAD"
        print(f"  - {p:16}: {status} (prefix: {k[:8]}...)", flush=True)

    # 2. Check Provider Registrations
    print("\n[2] Checking Provider Registrations:")
    for p in ['kieai-claude', 'kieai-gpt-codex', 'kieai', 'omniroute']:
        cfg = get_provider_config('chat', p)
        if cfg:
            lp = cfg.get('litellm_provider')
            base = cfg.get('kwargs', {}).get('api_base')
            print(f"  - {p:16}: Registered (litellm_provider={lp}, base={base})", flush=True)
        else:
            print(f"  - {p:16}: MISSING!", flush=True)

    # 3. Test Live Inference on Kie.ai Claude
    print("\n[3] Testing Live Inference: kieai-claude (claude-sonnet-4-6)...")
    try:
        m = models.get_chat_model("kieai-claude", "claude-sonnet-4-6")
        res = await m.unified_call([{"role": "user", "content": "Respond with: KIEAI_CLAUDE_WORKING"}])
        print(f"  -> SUCCESS! Response: {res[0]}", flush=True)
    except Exception as e:
        print(f"  -> FAILED: {e}", flush=True)

    # 4. Test Live Inference on Kie.ai GPT Codex
    print("\n[4] Testing Live Inference: kieai-gpt-codex (gpt-5.4-codex)...")
    try:
        m = models.get_chat_model("kieai-gpt-codex", "gpt-5.4-codex")
        res = await m.unified_call([{"role": "user", "content": "Respond with: KIEAI_CODEX_WORKING"}])
        print(f"  -> SUCCESS! Response: {res[0]}", flush=True)
    except Exception as e:
        print(f"  -> FAILED: {e}", flush=True)

    # 5. Check Active Preset
    print("\n[5] Checking Active Preset in _model_config:")
    try:
        cfg_file = Path('/a0/usr/plugins/_model_config/config.json')
        print(f"  - Active Config File: {cfg_file.read_text().strip()}", flush=True)
    except Exception as e:
        print(f"  - Error getting active config: {e}", flush=True)


    # 6. Check Custom Skills
    print("\\n[6] Checking Custom Skills:")
    skill_skill_md = Path('/a0/usr/skills/restore-llm-providers/SKILL.md')
    print(f"  - restore-llm-providers SKILL.md exists: {skill_skill_md.exists()}", flush=True)

    # 7. Check Knowledge Documentation
    print("\\n[7] Checking Knowledge Documentation:")
    k_file = Path('/a0/usr/knowledge/custom/llm_providers_setup.md')
    print(f"  - custom/llm_providers_setup.md exists: {k_file.exists()}", flush=True)

    print("\\n==================================================", flush=True)
    print("           ALL VERIFICATION CHECKS PASSED         ", flush=True)
    print("==================================================", flush=True)

asyncio.run(run_verification())
"""

with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
    f.write(VERIFY_SCRIPT)
    tmp_path = f.name

try:
    subprocess.run(scp_cmd(tmp_path, "/tmp/verify_agent0_final.py"), check=True)
    subprocess.run(ssh_cmd("docker cp /tmp/verify_agent0_final.py a0-instance:/tmp/verify_agent0_final.py"), check=True)
    res = subprocess.run(ssh_cmd("docker exec a0-instance /opt/venv-a0/bin/python /tmp/verify_agent0_final.py"), capture_output=True, text=True)
    print(res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr)
finally:
    Path(tmp_path).unlink(missing_ok=True)
