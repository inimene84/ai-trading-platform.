#!/usr/bin/env bash
set -euo pipefail
cd /root/ai-trading-platform-v3
docker compose -f docker-compose.prod.yml up -d --force-recreate influxdb
sleep 6
curl -sf http://127.0.0.1:8086/health | head -c 120
echo
TOKEN=$(grep '^INFLUXDB_TOKEN=' .env | cut -d= -f2-)
curl -sf -H "Authorization: Token ${TOKEN}" \
  'http://127.0.0.1:8086/api/v2/query?org=hedge-fund' \
  -H 'Content-Type: application/vnd.flux' \
  -d 'from(bucket:"trading-system") |> range(start: -30d) |> limit(n:3)' | head -c 400
echo
docker network connect trading-net grafana-k9xk-grafana-1 2>/dev/null || true
OLD_PASS=$(grep '^GF_SECURITY_ADMIN_PASSWORD=' /docker/grafana-k9xk/.env | cut -d= -f2-)
./scripts/fix_grafana_influx.sh http://127.0.0.1:3000 "admin:${OLD_PASS}" .
echo "Historical Influx restored. Open http://72.60.18.113:3000"
