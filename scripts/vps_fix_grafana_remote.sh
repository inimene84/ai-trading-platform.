#!/usr/bin/env bash
set -euo pipefail
cd "${PROJECT_DIR:-/root/ai-trading-platform-v3}"

echo "=== Influx ==="
TOKEN=$(grep '^INFLUXDB_TOKEN=' .env | cut -d= -f2-)
ORG=$(grep '^INFLUXDB_ORG=' .env | cut -d= -f2-)
echo "org=$ORG token_len=${#TOKEN}"
docker exec vps-influxdb influx bucket list --org "$ORG" --token "$TOKEN" 2>&1 | head -10

echo ""
echo "=== Old Grafana :3000 datasources ==="
curl -sf -u admin:admin http://127.0.0.1:3000/api/datasources | python3 -c "
import sys,json
for d in json.load(sys.stdin):
    print(d.get('name'), '|', d.get('url'), '|', d.get('type'))
" 2>&1 || echo "(auth failed - try other password)"

echo ""
echo "=== Old Grafana dashboards ==="
curl -sf -u admin:admin 'http://127.0.0.1:3000/api/search?type=dash-db' | python3 -c "
import sys,json
for x in json.load(sys.stdin):
    print(x.get('title'), x.get('uid'))
" 2>&1 | head -15

echo ""
echo "=== New Grafana datasources ==="
curl -sf -u admin:admin http://127.0.0.1:8081/grafana/api/datasources | python3 -c "
import sys,json
ds=json.load(sys.stdin)
print('count', len(ds))
for d in ds:
    print(d.get('name'), d.get('url'))
" 2>&1
