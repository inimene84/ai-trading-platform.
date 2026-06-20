#!/usr/bin/env bash
set -euo pipefail
# Try Grafana admin credentials on old stack.
# Provide candidates via GRAFANA_PROBE_AUTHS (space-separated user:pass), e.g.
#   GRAFANA_PROBE_AUTHS="admin:admin admin:$GRAFANA_PASS" ./vps_probe_old_grafana.sh
for auth in ${GRAFANA_PROBE_AUTHS:-admin:admin}; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -u "$auth" http://127.0.0.1:3000/api/org)
  echo "auth=$auth -> HTTP $code"
  if [ "$code" = "200" ]; then
    echo "=== datasources ==="
    curl -sf -u "$auth" http://127.0.0.1:3000/api/datasources | python3 -c "
import sys,json
for d in json.load(sys.stdin):
    jd=d.get('jsonData') or {}
    print(d.get('name'), '| url:', d.get('url'), '| org:', jd.get('organization'), '| bucket:', jd.get('defaultBucket'))
"
    echo "=== dashboards ==="
    curl -sf -u "$auth" 'http://127.0.0.1:3000/api/search?type=dash-db' | python3 -c "
import sys,json
for x in json.load(sys.stdin):
    print('-', x.get('title'))
"
    break
  fi
done
