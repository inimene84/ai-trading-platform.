#!/usr/bin/env bash
# Lightweight real-time health watchdog for VPS (run via cron every 2–5 min)
# Example: */3 * * * * /root/ai-trading-platform-v3/scripts/vps_realtime_watchdog.sh >> /var/log/quantumtrade-watchdog.log 2>&1
#
# GOLDEN RULE: halt trading + cancel orders BEFORE restarting backend.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/ai-trading-platform-v3}"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8001/health}"
NGINX_URL="${NGINX_URL:-http://127.0.0.1:8081/api/backend/trading/status}"
SENTRY_TOKEN="${SENTRY_WATCHDOG_TOKEN:-}"
LOG_TAG="[quantumtrade-watchdog $(date -Iseconds)]"

check() {
  local url="$1"
  curl -sf --max-time 10 "$url" >/dev/null
}

emergency_halt() {
  local reason="${1:-watchdog_cron_backend_unhealthy}"
  local base="${BACKEND_URL%/health}"
  echo "$LOG_TAG EMERGENCY HALT: $reason"
  if curl -sf --max-time 20 -X POST \
    -H "Content-Type: application/json" \
    ${SENTRY_TOKEN:+-H "X-Sentry-Token: $SENTRY_TOKEN"} \
    -d "{\"reason\":\"$reason\",\"source\":\"vps_realtime_watchdog\",\"manual\":false}" \
    "$base/sentry/emergency-halt" >/dev/null 2>&1; then
    echo "$LOG_TAG emergency-halt accepted by backend"
    return 0
  fi
  echo "$LOG_TAG backend emergency-halt failed (may be down); sentry_watchdog container handles local halt"
  return 1
}

restart_service() {
  local svc="$1"
  echo "$LOG_TAG restarting $svc"
  cd "$PROJECT_DIR" && docker compose -f docker-compose.prod.yml restart "$svc" 2>&1 || true
}

if ! check "$BACKEND_URL"; then
  echo "$LOG_TAG backend unhealthy"
  emergency_halt "cron_backend_health_failed" || true
  sleep 5
  restart_service backend
  sleep 15
fi

if ! check "$NGINX_URL"; then
  echo "$LOG_TAG nginx proxy unhealthy"
  restart_service nginx
fi

# Optional: ensure trading loop is running (paper mode safe default)
if check "$BACKEND_URL"; then
  status=$(curl -sf --max-time 10 "${BACKEND_URL%/health}/trading/status" 2>/dev/null || echo "")
  sentry=$(curl -sf --max-time 10 "${BACKEND_URL%/health}/sentry/status" 2>/dev/null || echo "")
  if echo "$status" | grep -q '"running":false'; then
    echo "$LOG_TAG trading loop not running (start via dashboard or API if desired)"
  fi
  if echo "$sentry" | grep -q 'HALTED'; then
    echo "$LOG_TAG trading is HALTED by sentry — manual /sentry/resume required after reconciliation"
  fi
fi
