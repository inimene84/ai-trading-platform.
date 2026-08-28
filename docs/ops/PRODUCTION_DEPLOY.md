# Production Deploy — QuantumTrade Pro

This document describes how to deploy QuantumTrade Pro to the Hostinger VPS,
how to roll back, and the safety gates that protect the **live real-money
trading bot**.

## Current state (2026-08-28)

- **Deploy is manual only.** The `VPS Deploy` GitHub Action is triggered via
  `workflow_dispatch` (Actions tab → "Run workflow" → type `DEPLOY`). It is
  **not** triggered by pushes to `main`.
- **The `SSH_PRIVATE_KEY` secret is not yet configured in GitHub**, so the
  Action cannot reach the VPS. Until it is configured, deploys must be run
  directly on the VPS (see below).
- The bot runs live (`PAPER_TRADING=false`, `DRY_RUN_ALL=false`) on Binance
  USDC-margined perpetuals. Treat every deploy as touching real money.

## Safety gates (already enforced)

These live in `scripts/hostinger_vps_apply.sh` and the application itself:

1. `.env` must exist and contain `ADMIN_API_KEY`, `LITELLM_API_KEY`,
   `QDRANT_API_KEY`.
2. A live deploy requires `CONFIRM_LIVE_DEPLOY=true` in the server-side `.env`.
   If `PAPER_TRADING=false` and `DRY_RUN_ALL=false` but
   `CONFIRM_LIVE_DEPLOY=true` is missing, the deploy aborts.
3. Risk Guard, Kill Switch, and the 9-gate fail-closed stack run in the app at
   every cycle (see README "Hard Safety Gates & Risk Management").

## How to deploy (GitHub Actions — recommended)

1. Configure repo secrets: `SSH_PRIVATE_KEY`, `SSH_HOST`, `SSH_USER`.
   Optionally create a `production` environment with required reviewers
   (Settings → Environments → production → Required reviewers).
2. Push your changes to `main` (Backend CI / Frontend CI must be green).
3. Actions tab → "VPS Deploy" → Run workflow → type `DEPLOY`.
4. Watch the run; the deploy runs `hostinger_vps_apply.sh` on the VPS:
   git pull → rebuild backend image → recreate containers → health check.

## How to deploy (directly on the VPS)

```bash
ssh root@72.60.18.113
cd /root/ai-trading-platform-v3
git fetch origin main && git checkout main && git pull origin main
./scripts/hostinger_vps_apply.sh
```

## Rollback

```bash
cd /root/ai-trading-platform-v3
# Find the last known-good commit
git log --oneline -10
git checkout <good-commit>
./scripts/hostinger_vps_apply.sh
```

Containers are recreated from the image built at the checked-out commit, so a
rollback is just `git checkout` + re-apply.

## Repo name caveat

The GitHub repository name is literally `ai-trading-platform.` (trailing
period). Renaming it is an external migration (changes clone URLs, the
`github.repository` guard, the VPS git remote, docs, and local clones) and is
**not** done automatically. If you want to rename it, coordinate it as a
separate step and update all references in one commit.
