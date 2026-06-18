#!/usr/bin/env bash
# Point Grafana InfluxDB datasources at the live vps-influxdb instance.
# Usage: ./fix_grafana_influx.sh <GRAFANA_URL> <ADMIN_USER:PASSWORD> [PROJECT_DIR]
set -euo pipefail

GRAFANA_URL=${1:-"http://localhost:3000"}
AUTH=${2:-"admin:admin"}
PROJECT_DIR=${3:-"/root/ai-trading-platform-v3"}

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  echo "ERROR: $PROJECT_DIR/.env not found"
  exit 1
fi

GRAFANA_INFLUX_URL="http://vps-influxdb:8086"
INFLUX_ORG=$(grep '^INFLUXDB_ORG=' "$PROJECT_DIR/.env" | cut -d= -f2-)
INFLUX_TOKEN=$(grep '^INFLUXDB_TOKEN=' "$PROJECT_DIR/.env" | cut -d= -f2-)
if [ "$INFLUX_ORG" = "819d45e061531bd6" ] || [ -z "$INFLUX_ORG" ]; then
  INFLUX_ORG="hedge-fund"
fi

echo "Fixing Grafana datasources → $GRAFANA_INFLUX_URL (org=$INFLUX_ORG)"

export GRAFANA_URL AUTH GRAFANA_INFLUX_URL INFLUX_ORG INFLUX_TOKEN
python3 <<'PY'
import json, os, urllib.request, base64

grafana_url = os.environ["GRAFANA_URL"]
auth = os.environ["AUTH"]
influx_url = os.environ["GRAFANA_INFLUX_URL"]
org = os.environ["INFLUX_ORG"]
token = os.environ["INFLUX_TOKEN"]
headers = {"Authorization": "Basic " + base64.b64encode(auth.encode()).decode()}

bucket_map = {
    "influxdb": "trading-system",
    "influxdb - memory": "trading-memory",
    "influxdb - news": "news-sentiment",
    "influxdb - orders": "trading-orders",
    "influxdb - signals": "trading-signals",
    "influxdb - system": "trading-system",
    "influxdb - raw": "trading-raw",
}

req = urllib.request.Request(f"{grafana_url}/api/datasources", headers=headers)
with urllib.request.urlopen(req) as resp:
    datasources = json.loads(resp.read())

updated = 0
for ds in datasources:
    if ds.get("type") != "influxdb":
        continue
    name = ds.get("name", "")
    default_bucket = bucket_map.get(name.lower(), ds.get("jsonData", {}).get("defaultBucket", "trading-system"))
    payload = {
        "id": ds["id"],
        "uid": ds.get("uid"),
        "name": name,
        "type": "influxdb",
        "access": "proxy",
        "url": influx_url,
        "isDefault": ds.get("isDefault", False),
        "jsonData": {
            "version": "Flux",
            "organization": org,
            "defaultBucket": default_bucket,
            "httpMode": "POST",
        },
        "secureJsonData": {"token": token},
    }
    put_headers = {**headers, "Content-Type": "application/json"}
    uid = ds.get("uid")
    if not uid:
        print(f"  skip {name}: no uid")
        continue
    payload.pop("id", None)
    put = urllib.request.Request(
        f"{grafana_url}/api/datasources/uid/{uid}",
        data=json.dumps(payload).encode(),
        headers=put_headers,
        method="PUT",
    )
    with urllib.request.urlopen(put) as r:
        json.loads(r.read())
        print(f"  updated {name} → {influx_url} bucket={default_bucket}")
        updated += 1

print(f"Done: {updated} datasource(s) updated")
PY

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR/deploy_grafana.sh" "$GRAFANA_URL" "$AUTH"

echo ""
echo "Verify: $GRAFANA_URL/d/ai_trading_v1/ai-trading-platform (time range: Last 24h)"
