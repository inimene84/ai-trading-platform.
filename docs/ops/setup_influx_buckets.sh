#!/bin/bash
# Create InfluxDB buckets for the trading platform

INFLUX_URL="http://localhost:8086"
TOKEN="***REMOVED***"
ORG="hedge-fund"

# Buckets with their retention policies
buckets=(
    "trading-signals:90d"
    "trading-orders:365d"
    "trading-raw:30d"
    "trading-memory:0"  # unlimited
    "trading-system:7d"
)

echo "Creating InfluxDB buckets..."
for bucket_info in "${buckets[@]}"; do
    bucket="${bucket_info%%:*}"
    days="${bucket_info##*:}"
    
    # Create bucket
    curl -s -X POST "$INFLUX_URL/api/v2/buckets" \
        -H "Authorization: Token $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"$bucket\",\"org\":\"$ORG\",\"retention_rules\":[{\"type\":\"expire\",\"every_seconds\":$((days * 86400)),\"shard_group_duration_seconds\":86400}]}" | jq .
    
    echo "Created bucket: $bucket (retention: $days)"
done

echo "Done!"