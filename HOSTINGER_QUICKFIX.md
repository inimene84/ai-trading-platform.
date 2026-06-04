# Hostinger VPS quick fix (72.60.18.113)

## Diagnosis (verified remotely)

| Check | Result |
|-------|--------|
| Backend `:8001/health` | OK |
| Trading loop | **Running** (15m interval, 10 symbols, cycle #30+) |
| Mode | **live**, `dry_run: false` |
| Nginx `:8081/api/backend/trading/status` | **404** — dashboard API broken |
| Fix | Deploy updated `nginx.conf` from branch `cursor/hostinger-vps-fixes-df88` |

## One command (from your PC, after SSH key works)

```powershell
ssh -i C:\Users\thori\New root@72.60.18.113 "cd /root/ai-trading-platform-v3 && git fetch origin && git checkout cursor/hostinger-vps-fixes-df88 && chmod +x scripts/hostinger_vps_apply.sh && ./scripts/hostinger_vps_apply.sh"
```

## Cloud Agent SSH (optional)

Add this line to `/root/.ssh/authorized_keys` on the VPS:

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB8Tfjj7KDxsmm5deuH0WtDC+M9CoUyt8JqZKCWPjrr9 cursor-cloud-agent
```

Then re-run the Cloud Agent — it will use `./scripts/ssh_vps_remote.sh`.

## Security (recommended)

- Restrict port **8001** to localhost (API is currently public; loop start/stop is unauthenticated).
- Consider `DRY_RUN_ALL=true` or `PAPER_TRADING=true` in `.env` until nginx + dashboard are verified.
- Do not expose Docker port 2375 publicly if enabled.

## Temporary dashboard workaround

In the app **Settings**, set **BACKEND_URL** to `http://72.60.18.113:8001` (bypasses broken nginx proxy until fix is applied).
