#!/bin/bash
# Test script for verifying backend endpoints and service connectivity
set -e

echo "=== Checking container status ==="
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | head -20

echo -e "\n=== Waiting for backend to be ready ==="
for i in {1..30}; do
    if curl -s http://localhost:8001/health 2>/dev/null | jq -e '.status' > /dev/null 2>&1; then
        echo "Backend is ready!"
        break
    fi
    echo "Waiting... ($i/30)"
    sleep 2
done

echo -e "\n=== Testing localhost endpoints ==="

# Test backend health
echo -e "\n1. Backend health check:"
curl -s http://localhost:8001/health | jq .

# Test InfluxDB
echo -e "\n2. InfluxDB health:"
curl -s http://localhost:8086/health | jq .

# Test Qdrant
echo -e "\n3. Qdrant status:"
curl -s http://localhost:6333/collections | jq '.result'

echo -e "\n=== Testing sentiment endpoint with correct payload ==="
curl -s -X POST http://localhost:8001/api/news/sentiment \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","sentiment_score":0.5,"impact_score":0.75,"source":"test"}' | jq .

echo -e "\n=== Verify crypto-news collection ==="
curl -s http://localhost:6333/collections/crypto-news | jq '.result // "Will be created on first archive"'

echo -e "\n=== Done ==="