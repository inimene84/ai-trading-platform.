#!/usr/bin/env bash
# Train Jesse ML models on the VPS with strategy-aligned triple-barrier geometry.
# Required env: SSH_HOST, SSH_USER, SSH_PRIVATE_KEY (or SSH_PASSWORD)
#
# Usage:
#   ./scripts/jesse_vps_train.sh [SYMBOL] [TIMEFRAME] [PT_MULT] [SL_MULT]
# Example:
#   ./scripts/jesse_vps_train.sh BTC-USDT 1h 5.5 1.75
set -euo pipefail

SYMBOL="${1:-BTC-USDT}"
TIMEFRAME="${2:-1h}"
PT_MULT="${3:-5.5}"
SL_MULT="${4:-1.75}"
MODEL_TYPE="${5:-lightgbm}"

SSH_HOST="${SSH_HOST:-}"
if [[ -z "$SSH_HOST" ]]; then
  echo "Error: SSH_HOST environment variable is required." >&2
  exit 1
fi
SSH_USER="${SSH_USER:-root}"
SSH_PORT="${SSH_PORT:-22}"
JESSE_DIR="${JESSE_DIR:-/root/jesse-trading}"
KEY_FILE="${TMPDIR:-/tmp}/vps_ssh_key_$$"

cleanup() { rm -f "$KEY_FILE"; }
trap cleanup EXIT

if [[ -n "${SSH_PRIVATE_KEY:-}" ]]; then
  BEGIN_MARKER="-----BEGIN OPENSSH PRIVATE KEY-----"
  END_MARKER="-----END OPENSSH PRIVATE KEY-----"
  if [[ "$SSH_PRIVATE_KEY" != *$'\n'* && "$SSH_PRIVATE_KEY" == *"$BEGIN_MARKER"* ]]; then
    body="${SSH_PRIVATE_KEY//$BEGIN_MARKER/}"
    body="${body//$END_MARKER/}"
    body="${body// /$'\n'}"
    printf '%s\n%s\n%s\n' "$BEGIN_MARKER" "$body" "$END_MARKER" > "$KEY_FILE"
  else
    printf '%b\n' "$SSH_PRIVATE_KEY" > "$KEY_FILE"
  fi
  chmod 600 "$KEY_FILE"
  SSH_OPTS=(-i "$KEY_FILE" -o StrictHostKeyChecking=accept-new -p "$SSH_PORT")
elif [[ -n "${SSH_PASSWORD:-}" ]]; then
  command -v sshpass >/dev/null || { echo "Install sshpass or use SSH_PRIVATE_KEY"; exit 1; }
  SSH_OPTS=(-o StrictHostKeyChecking=accept-new -p "$SSH_PORT")
  SSH_PASS_CMD=(sshpass -p "$SSH_PASSWORD")
else
  echo "Set SSH_PRIVATE_KEY or SSH_PASSWORD" >&2
  exit 1
fi

REMOTE_CMD=$(cat <<EOF
set -e
cd ${JESSE_DIR}
echo "[*] Training ${SYMBOL} ${TIMEFRAME} with PT=${PT_MULT}x SL=${SL_MULT}x ATR (triple-barrier)"
./manage.sh train-tb-ml ${SYMBOL} ${TIMEFRAME} ${MODEL_TYPE} ${PT_MULT} ${SL_MULT}
./manage.sh clear-cache
./manage.sh predict-ml ${SYMBOL} ${TIMEFRAME}
./manage.sh backtest QuantumAIStrategy
EOF
)

echo "Connecting to ${SSH_USER}@${SSH_HOST}:${SSH_PORT} ..."
if [[ -n "${SSH_PASSWORD:-}" ]]; then
  "${SSH_PASS_CMD[@]}" ssh "${SSH_OPTS[@]}" "${SSH_USER}@${SSH_HOST}" "$REMOTE_CMD"
else
  ssh "${SSH_OPTS[@]}" "${SSH_USER}@${SSH_HOST}" "$REMOTE_CMD"
fi

echo "Jesse VPS training finished."
