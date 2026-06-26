import os, subprocess, tempfile

raw = os.environ.get("SSH_PRIVATE_KEY", "").strip()
if not raw:
    print("SSH_PRIVATE_KEY not found")
    exit(1)

begin = "-----BEGIN OPENSSH PRIVATE KEY-----"
end = "-----END OPENSSH PRIVATE KEY-----"
body = raw.replace(begin, "").replace(end, "").strip().replace(" ", "\n")
key_content = f"{begin}\n{body}\n{end}\n"

with tempfile.NamedTemporaryFile(mode='w', suffix='_key', delete=False) as f:
    f.write(key_content)
    key_path = f.name
os.chmod(key_path, 0o600)

host = os.environ.get("SSH_HOST", "72.60.18.113")
user = os.environ.get("SSH_USER", "root")

remote_python = """
import os

env_path = "/root/ai-trading-platform-v3/.env"
if not os.path.exists(env_path):
    print("No .env found at", env_path)
    exit(1)

with open(env_path, "r") as f:
    lines = f.readlines()

new_lines = []
keys_to_remove = {"PERSONA_LLM_PROVIDER", "DEEP_ANALYSIS_LLM_PROVIDER", "GENERAL_LLM_PROVIDER", "KIE_BASE_URL", "KIE_MODEL"}

for line in lines:
    keep = True
    for key in keys_to_remove:
        if line.startswith(key + "="):
            keep = False
    if keep:
        new_lines.append(line)

new_lines.append("PERSONA_LLM_PROVIDER=kie\n")
new_lines.append("DEEP_ANALYSIS_LLM_PROVIDER=kie\n")
new_lines.append("GENERAL_LLM_PROVIDER=kie\n")
new_lines.append("KIE_MODEL=claude-sonnet-4-6\n")
new_lines.append("KIE_BASE_URL=https://api.kie.ai/claude\n")

with open(env_path, "w") as f:
    f.writelines(new_lines)

print("Updated .env")
"""

cmd = [
    "ssh", "-i", key_path, "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
    f"{user}@{host}",
    f"python3 -c '{remote_python}' && cd /root/ai-trading-platform-v3 && git pull && docker compose -f docker-compose.prod.yml restart backend"
]

r = subprocess.run(cmd, capture_output=True, text=True)
print(r.stdout)
if r.stderr:
    print("ERR:", r.stderr)

os.unlink(key_path)
