#!/usr/bin/env bash
# Run from Cursor Cloud Agent when SSH secrets are configured.
# Required env: SSH_HOST, SSH_USER, SSH_PRIVATE_KEY (or SSH_PASSWORD)
# Optional: SSH_PORT (default 22), PROJECT_DIR (default /root/ai-trading-platform-v3)
set -euo pipefail

: "${SSH_HOST:?Set SSH_HOST (VPS IP or hostname)}"
: "${SSH_USER:?Set SSH_USER (e.g. root)}"

SSH_PORT="${SSH_PORT:-22}"
PROJECT_DIR="${PROJECT_DIR:-/root/ai-trading-platform-v3}"
KEY_FILE="${TMPDIR:-/tmp}/vps_ssh_key_$$"

cleanup() { rm -f "$KEY_FILE"; }
trap cleanup EXIT

if [[ -n "${SSH_PRIVATE_KEY:-}" ]]; then
  printf '%s\n' "$SSH_PRIVATE_KEY" > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  SSH_OPTS=(-i "$KEY_FILE" -o StrictHostKeyChecking=accept-new -p "$SSH_PORT")
elif [[ -n "${SSH_PASSWORD:-}" ]]; then
  command -v sshpass >/dev/null || { echo "Install sshpass or use SSH_PRIVATE_KEY"; exit 1; }
  SSH_OPTS=(-o StrictHostKeyChecking=accept-new -p "$SSH_PORT")
  SSH_PASS_CMD=(sshpass -p "$SSH_PASSWORD")
else
  echo "Set SSH_PRIVATE_KEY or SSH_PASSWORD"
  exit 1
fi

REMOTE_CMD="cd ${PROJECT_DIR} && git pull origin main && chmod +x scripts/hostinger_vps_apply.sh scripts/vps_realtime_watchdog.sh && PROJECT_DIR=${PROJECT_DIR} ./scripts/hostinger_vps_apply.sh"

echo "Connecting to ${SSH_USER}@${SSH_HOST}:${SSH_PORT} ..."
if [[ -n "${SSH_PASSWORD:-}" ]]; then
  "${SSH_PASS_CMD[@]}" ssh "${SSH_OPTS[@]}" "${SSH_USER}@${SSH_HOST}" "$REMOTE_CMD"
else
  ssh "${SSH_OPTS[@]}" "${SSH_USER}@${SSH_HOST}" "$REMOTE_CMD"
fi

echo "Remote apply finished."
