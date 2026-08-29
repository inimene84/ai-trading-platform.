"""Fix all OmniRoute and Kie.ai presets in Agent Zero presets.yaml."""

import subprocess
import tempfile
from pathlib import Path
from scripts.vps_ssh_common import ssh_cmd, scp_cmd

PATCH_PRESETS = """import yaml, json, os
from pathlib import Path

presets_path = Path("/var/lib/docker/volumes/agent-zero_a0-data/_data/plugins/_model_config/presets.yaml")

# Load existing presets
with open(presets_path, "r", encoding="utf-8") as f:
    presets = yaml.safe_load(f) or []

# Define working configs
for p in presets:
    name = p.get("name", "")
    
    # Fix Kie.ai presets
    if name == "Kie.ai Sonnet":
        p["chat"] = {
            "provider": "kieai-claude",
            "name": "claude-sonnet-4-6",
            "ctx_length": 200000,
            "ctx_history": 0.7,
            "vision": True,
            "max_tokens": 4096,
            "temperature": 0.7
        }
        p["utility"] = {
            "provider": "kieai",
            "name": "claude-haiku-4-5"
        }
    elif name == "Kie.ai Codex 5.4":
        p["chat"] = {
            "provider": "kieai-gpt-codex",
            "name": "gpt-5.4-codex",
            "ctx_length": 128000,
            "ctx_history": 0.7,
            "vision": True,
            "max_tokens": 4096,
            "temperature": 0.7
        }
        p["utility"] = {
            "provider": "kieai-gpt-codex",
            "name": "gpt-5.4-codex"
        }
    elif name == "Kie.ai Codex 5.1":
        p["chat"] = {
            "provider": "kieai-gpt-codex",
            "name": "gpt-5.1-codex",
            "ctx_length": 128000,
            "ctx_history": 0.7,
            "vision": True,
            "max_tokens": 4096,
            "temperature": 0.7
        }
        p["utility"] = {
            "provider": "kieai-gpt-codex",
            "name": "gpt-5.4-codex"
        }
    elif name == "Kie.ai Opus":
        p["chat"] = {
            "provider": "kieai-claude",
            "name": "claude-opus-4-6",
            "ctx_length": 200000,
            "ctx_history": 0.7,
            "vision": True,
            "max_tokens": 4096,
            "temperature": 0.7
        }
        p["utility"] = {
            "provider": "kieai",
            "name": "claude-sonnet-4-6"
        }
    elif name == "Kie.ai Haiku":
        p["chat"] = {
            "provider": "kieai-claude",
            "name": "claude-haiku-4-5",
            "ctx_length": 200000,
            "ctx_history": 0.7,
            "vision": True,
            "max_tokens": 4096,
            "temperature": 0.7
        }
        p["utility"] = {
            "provider": "kieai",
            "name": "claude-haiku-4-5"
        }

    # CRITICAL: Fix OmniRoute presets so even if selected in UI, they use verified working models!
    elif name == "OmniRoute Claude Sonnet":
        p["chat"] = {
            "provider": "kieai-claude",
            "name": "claude-sonnet-4-6",
            "ctx_length": 200000,
            "ctx_history": 0.7,
            "vision": True,
            "max_tokens": 4096,
            "temperature": 0.7
        }
        p["utility"] = {
            "provider": "kieai",
            "name": "claude-haiku-4-5"
        }
    elif name == "OmniRoute Auto":
        p["chat"] = {
            "provider": "kieai-claude",
            "name": "claude-sonnet-4-6",
            "ctx_length": 200000,
            "ctx_history": 0.7,
            "vision": True,
            "max_tokens": 4096,
            "temperature": 0.7
        }
        p["utility"] = {
            "provider": "kieai",
            "name": "claude-haiku-4-5"
        }
    elif name == "OmniRoute GPT-4o":
        p["chat"] = {
            "provider": "kieai-gpt-codex",
            "name": "gpt-5.4-codex",
            "ctx_length": 128000,
            "ctx_history": 0.7,
            "vision": True,
            "max_tokens": 4096,
            "temperature": 0.7
        }
        p["utility"] = {
            "provider": "kieai-gpt-codex",
            "name": "gpt-5.4-codex"
        }
    elif name == "OmniRoute Gemini Flash":
        p["chat"] = {
            "provider": "openrouter",
            "name": "google/gemini-2.5-flash",
            "ctx_length": 128000,
            "ctx_history": 0.7,
            "vision": True,
            "max_tokens": 4096,
            "temperature": 0.7
        }
        p["utility"] = {
            "provider": "openrouter",
            "name": "google/gemini-2.5-flash-lite"
        }

with open(presets_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(presets, f, sort_keys=False, allow_unicode=True, default_flow_style=False)
print("Updated all presets in presets.yaml successfully")

# Set active preset in config.json
config_path = Path("/var/lib/docker/volumes/agent-zero_a0-data/_data/plugins/_model_config/config.json")
config_path.write_text(json.dumps({"model_preset": "Kie.ai Sonnet"}, indent=2), encoding="utf-8")
print("Set active preset to Kie.ai Sonnet in config.json")

# Restart container to clear cached chat loops and reload preset definitions
os.system("docker restart a0-instance")
print("Restarted a0-instance container")
"""

with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
    f.write(PATCH_PRESETS)
    tmp_path = f.name

try:
    subprocess.run(scp_cmd(tmp_path, "/tmp/patch_presets.py"), check=True)
    res = subprocess.run(ssh_cmd("python3 /tmp/patch_presets.py"), capture_output=True, text=True)
    print(res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr)
finally:
    Path(tmp_path).unlink(missing_ok=True)
