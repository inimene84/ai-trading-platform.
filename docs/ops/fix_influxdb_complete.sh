#!/bin/bash
set -e

echo "=============================================="
echo "SETUP COMPLETE INFLUXDB FOR TRADING PLATFORM"
echo "=============================================="

TOKEN="VGsJs1Ft4tLq1_kwcbodJy3kxNXs4KcbYv7kj4VLlDWsLtnPEwUNZ8vczuNnBT65iRObY4IlW5Ip7SJ04yHM3g=="
ORG="hedge-fund"
URL="http://localhost:8086"

# Function to create bucket
create_bucket() {
    local bucket=$1
    local days=$2
    echo "Creating bucket: $bucket (retention: $days days)"
    
    curl -s -X POST "$URL/api/v2/buckets" \
        -H "Authorization: Token $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"$bucket\",\"org\":\"$ORG\",\"retention_rules\":[{\"type\":\"expire\",\"every_seconds\":$((days * 86400)),\"shard_group_duration_seconds\":86400}]}" >/dev/null
    
    # Check if it already exists
    if curl -s "$URL/api/v2/buckets?org=$ORG" -H "Authorization: Token $TOKEN" | grep -q "$bucket"; then
        echo "  ✓ Bucket exists"
    else
        echo "  Created"
    fi
}

echo ""
echo "Creating required buckets..."
create_bucket "trading-signals" 90
create_bucket "trading-orders" 365
create_bucket "trading-raw" 30
create_bucket "trading-memory" 0  # unlimited
create_bucket "trading-system" 7

echo ""
echo "=============================================="
echo "REBUILD BACKEND"
echo "=============================================="

cd /root/ai-trading-platform-v3
docker compose down backend
docker compose up -d --build backend

echo ""
echo "Waiting for backend to start..."
sleep 30

echo ""
echo "Checking InfluxDB connection in backend..."
docker logs ai-trading-backend --tail 20 | grep -i influx || echo "No InfluxDB errors - GOOD!"

echo ""
echo "Verifying setup..."
curl -s http://localhost:8081/health | jq .