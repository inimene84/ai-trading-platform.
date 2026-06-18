# Hostinger VPS quick fix (72.60.18.113)

## Canonical deploy (main branch)

Run on the VPS as root (Hostinger browser terminal or SSH):

```bash
cd /root/ai-trading-platform-v3
git pull origin main
chmod +x scripts/hostinger_vps_apply.sh scripts/lib/vps_ssh_hygiene.sh
./scripts/hostinger_vps_apply.sh
```

One-liner from anywhere:

```bash
curl -fsSL "https://raw.githubusercontent.com/inimene84/ai-trading-platform./main/scripts/vps_bootstrap_and_deploy.sh" | bash
```

From your PC (when SSH works):

```powershell
ssh root@72.60.18.113 "cd /root/ai-trading-platform-v3 && git pull origin main && ./scripts/hostinger_vps_apply.sh"
```

## Deprecated scripts

Do **not** use old branch-specific scripts (`cursor/observability-env-vars-f7d8`, etc.).
`vps_deploy_p0_oneliner.sh` and `vps_deploy_risk_reviewer_fix.sh` now forward to `hostinger_vps_apply.sh`.

## Security (recommended)

Set `ADMIN_API_KEY` in VPS `.env` to protect loop start/stop and config writes.
Add the same value in dashboard **Settings** as `ADMIN_API_KEY` so POST requests include `X-API-Key`.
Restrict port **8001** to localhost when nginx proxies the dashboard on **8081**.

## Cloud Agent SSH

Add the cloud-agent public key to `/root/.ssh/authorized_keys`, then run `./scripts/ssh_vps_remote.sh`.
