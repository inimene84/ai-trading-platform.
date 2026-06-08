# Cloud Agent Instructions

## SSH Access to Hostinger VPS

SSH is configured and working. Use the `scripts/ssh_vps_remote.sh` script or connect directly.

### Secrets Required

These secrets should be configured in Cursor Cloud Agent settings:
- `SSH_HOST` - VPS IP address
- `SSH_USER` - `root`
- `SSH_PRIVATE_KEY` - ed25519 private key (may be stored as single line with spaces)

### Quick SSH Test

```python
import os, subprocess, tempfile

raw = os.environ.get("SSH_PRIVATE_KEY", "").strip()
begin = "-----BEGIN OPENSSH PRIVATE KEY-----"
end = "-----END OPENSSH PRIVATE KEY-----"
body = raw.replace(begin, "").replace(end, "").strip().replace(" ", "\n")
key_content = f"{begin}\n{body}\n{end}\n"

with tempfile.NamedTemporaryFile(mode='w', suffix='_key', delete=False) as f:
    f.write(key_content)
    key_path = f.name
os.chmod(key_path, 0o600)

host = os.environ["SSH_HOST"]
user = os.environ["SSH_USER"]
cmd = ["ssh", "-i", key_path, "-o", "StrictHostKeyChecking=accept-new",
       "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
       f"{user}@{host}", "hostname && docker ps --format 'table {{.Names}}\t{{.Status}}'"]
r = subprocess.run(cmd, capture_output=True, text=True)
print(r.stdout or r.stderr)
os.unlink(key_path)
```

### SSH Troubleshooting

If SSH fails with "Permission denied", check VPS auth log:
```bash
tail -20 /var/log/auth.log | grep ssh
```

Common issues:
| Error | Fix |
|-------|-----|
| `bad ownership or modes for directory /root` | `chown root:root /root && chmod 700 /root` |
| `bad ownership or modes for file authorized_keys` | `chmod 600 /root/.ssh/authorized_keys` |
| Key not in authorized_keys | Add public key to `/root/.ssh/authorized_keys` |
| Key stored as single line | Script handles this automatically |

### VPS Services

The VPS runs these Docker containers:
- `ai-trading-backend` - FastAPI backend on port 8001
- `ai-trading-nginx` - Reverse proxy on port 8081
- `ai-trading-litellm` - LLM proxy
- `ai-trading-redis` - Cache
- `vps-influxdb` - Time series DB
- `vps-qdrant` - Vector DB
- `grafana-*` - Monitoring

### Deployment

Run `./scripts/ssh_vps_remote.sh` to deploy latest changes from main branch to VPS.

## Cursor Cloud Specific Instructions

### Key Normalization

The `SSH_PRIVATE_KEY` secret may be stored as a single line with spaces instead of newlines. The `scripts/ssh_vps_remote.sh` script automatically reformats OpenSSH keys before use.

### Testing Backend API

The backend is accessible at `http://<SSH_HOST>:8001`:
```bash
curl -sf "http://${SSH_HOST}:8001/health"
curl -sf "http://${SSH_HOST}:8001/openapi.json" | head -c 500
```
