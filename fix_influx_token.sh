#!/bin/bash
set -e

echo "============================================"
echo "FIX INFLUXDB TOKEN + CREATE MISSING BUCKETS"
echo "============================================"

# Get actual admin token
echo "Getting admin token..."
ADMIN_TOKEN=$(curl -s -X POST http://localhost:8086/api/v2/signin \
  -d '{"username":"admin","password":"hedgefund123"}' | jq -r '.token')

echo "Admin token: ${ADMIN_TOKEN:0:20}..."

# Get org ID
ORG_ID=$(curl -s http://localhost:8086/api/v2/orgs \
  -H "Authorization: Token $ADMIN_TOKEN" | jq -r '.orgs[] | select(.name=="hedge-fund") | .id')
echo "Org ID: $ORG_ID"

# Create trading-system bucket (the one that's missing!)
echo "Creating trading-system bucket..."
curl -s -X POST http://localhost:8086/api/v2/buckets \
  -H "Authorization: Token $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"trading-system","orgID":"'$ORG_ID'","retention_rules":[{"type":"expire","every_seconds":604800}]}' | jq '.name // .'

# Test write
echo "Testing write..."
curl -s -X POST http://localhost:8086/api/v2/write \
  -H "Authorization: Token $ADMIN_TOKEN" \
  --data-raw 'binance_wallet,broker=test balance=100.0,equity=100.0,available=100.0' \
  -G --data-urlencode "org=hedge-fund" \
  -G --data-urlencode "bucket=trading-system" \
  -G --data-urlencode "precision=s" && echo "Write OK!"

echo "============================================"
echo "Now update .env with the CORRECT TOKEN:"
echo "INFLUXDB_TOKEN=$ADMIN_TOKEN"
echo "============================================"