#!/usr/bin/env bash
set -euo pipefail
cd /root/ai-trading-platform-v3

TOKEN=$(grep '^INFLUXDB_TOKEN=' .env | cut -d= -f2-)
echo "=== Influx orgs (API) ==="
ORGS_JSON=$(curl -sf -H "Authorization: Token ${TOKEN}" http://127.0.0.1:8086/api/v2/orgs)
echo "$ORGS_JSON" | python3 -c "
import sys,json
data=json.load(sys.stdin)
for o in data.get('orgs',[]):
    print(o['name'], o['id'])
"

ORG_NAME=$(echo "$ORGS_JSON" | python3 -c "import sys,json; o=json.load(sys.stdin)['orgs'][0]; print(o['name'])")
ORG_ID=$(echo "$ORGS_JSON" | python3 -c "import sys,json; o=json.load(sys.stdin)['orgs'][0]; print(o['id'])")
echo "Using org name=$ORG_NAME id=$ORG_ID"

# Fix .env if org was stored as ID string
CURRENT_ORG=$(grep '^INFLUXDB_ORG=' .env | cut -d= -f2-)
if [ "$CURRENT_ORG" != "$ORG_NAME" ]; then
  sed -i "s|^INFLUXDB_ORG=.*|INFLUXDB_ORG=${ORG_NAME}|" .env
  echo "Updated .env INFLUXDB_ORG -> $ORG_NAME"
fi

./scripts/ensure_influx_buckets.sh

OLD_GRAFANA_PASS=$(grep '^GF_SECURITY_ADMIN_PASSWORD=' /docker/grafana-k9xk/.env | cut -d= -f2-)
echo ""
echo "=== Fix OLD Grafana (:3000) datasources ==="
./scripts/fix_grafana_influx.sh "http://127.0.0.1:3000" "admin:${OLD_GRAFANA_PASS}" .

sed -i 's|^GRAFANA_URL=.*|GRAFANA_URL=http://72.60.18.113:3000|' .env

echo ""
echo "=== Verify old grafana datasources ==="
curl -sf -u "admin:${OLD_GRAFANA_PASS}" http://127.0.0.1:3000/api/datasources | python3 -c "
import sys,json
for d in json.load(sys.stdin):
    if d.get('type')=='influxdb':
        jd=d.get('jsonData') or {}
        print(d['name'], '->', d.get('url'), 'org=', jd.get('organization'), 'bucket=', jd.get('defaultBucket'))
"

# Restart backend so it uses fixed INFLUXDB_ORG
docker compose -f docker-compose.prod.yml up -d backend

echo ""
echo "Done. Use OLD Grafana with dashboards: http://72.60.18.113:3000"
echo "Login: admin / (see /docker/grafana-k9xk/.env)"
