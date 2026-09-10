# Cloud Agent Instructions

SSH access lives in private operator docs, not this repository.

Required GitHub Actions secrets (no hardcoded host or user):

- `SSH_HOST`
- `SSH_USER` (non-root deploy user)
- `SSH_PRIVATE_KEY`
- `SSH_KNOWN_HOSTS` (pinned host key)

Deploy only through `.github/workflows/vps-deploy.yml` after typing `DEPLOY`.
Do not reconstruct private keys in chat logs or committed scripts.

Backend health (replace host locally):

```bash
curl -sf "http://${SSH_HOST}:8001/health"
```
