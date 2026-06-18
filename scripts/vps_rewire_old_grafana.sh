#!/usr/bin/env bash
set -euo pipefail
sed -i 's/\r$//' /root/ai-trading-platform-v3/scripts/fix_grafana_influx.sh 2>/dev/null || true
OLD_PASS=$(grep '^GF_SECURITY_ADMIN_PASSWORD=' /docker/grafana-k9xk/.env | cut -d= -f2-)
docker network connect trading-net grafana-k9xk-grafana-1 2>/dev/null || true
cd /root/ai-trading-platform-v3
bash scripts/fix_grafana_influx.sh "http://127.0.0.1:3000" "admin:${OLD_PASS}" .
