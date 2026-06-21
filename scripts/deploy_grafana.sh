#!/bin/bash
# Deploy ai_trading_dashboard.json to a Grafana instance using its HTTP API.
# Usage: ./deploy_grafana.sh <GRAFANA_URL> <ADMIN_USER>:<ADMIN_PASSWORD>

GRAFANA_URL=${1:-"http://localhost:3000"}
AUTH=${2:-"admin:admin"}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_FILE="${SCRIPT_DIR}/../grafana/dashboards/ai_trading_dashboard.json"

if [ ! -f "$DASHBOARD_FILE" ]; then
  echo "Error: $DASHBOARD_FILE not found."
  exit 1
fi

echo "Deploying dashboard to $GRAFANA_URL..."

# Wrap the dashboard JSON in the format expected by the Grafana API
PAYLOAD=$(cat <<EOF
{
  "dashboard": $(cat "$DASHBOARD_FILE"),
  "folderId": 0,
  "overwrite": true
}
EOF
)

RESPONSE=$(curl -s -X POST -u "$AUTH" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "$GRAFANA_URL/api/dashboards/db")

if echo "$RESPONSE" | grep -q '"status":"success"'; then
  echo "Dashboard deployed successfully!"
  echo "Response: $RESPONSE"
else
  echo "Failed to deploy dashboard."
  echo "Response: $RESPONSE"
  exit 1
fi
