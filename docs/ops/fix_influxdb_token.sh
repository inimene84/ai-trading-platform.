#!/bin/bash
# Get correct InfluxDB token and update .env

echo "Fetching working InfluxDB token..."
TOKEN=$(curl -s -X POST "http://localhost:8086/api/v2/signin" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"hedgefund123"}' | jq -r '.token')

if [ "$TOKEN" = "null" ] || [ -z "$TOKEN" ]; then
    echo "ERROR: Could not get token"
    exit 1
fi

echo "Got token: ${TOKEN:0:30}..."

# Update .env
sed -i "s/^INFLUXDB_TOKEN=.*/INFLUXDB_TOKEN=$TOKEN/" .env

echo "Updated .env"
grep INFLUXDB_TOKEN .env

# Test write
echo "Testing write..."
curl -s -X POST "http://localhost:8086/api/v2/write" \
  -H "Authorization: Token $TOKEN" \
  --data-raw 'test_point value=1.0' \
  -G --data-urlencode "org=hedge-fund" \
  -G --data-urlencode "bucket=trading-system" \
  -G --data-urlencode "precision=s" && echo "Write OK!" || echo "Write FAILED"

# Restart backend
echo "Restarting backend..."
docker compose restart backend

sleep 10

# Check logs
docker logs ai-trading-backend --tail 15 | grep -i "influx\|404" || echo "✓ No InfluxDB errors!"