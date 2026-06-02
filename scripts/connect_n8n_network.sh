#!/bin/bash
# Fix script: Connect n8n to trading-net and verify all services
# Run this on the VPS

set -e

echo "=== Connecting n8n to trading-net ==="

# Get n8n container name (may vary)
N8N_CONTAINER=$(docker ps --filter "name=n8n" --format "{{.Names}}" | head -1)
echo "Found n8n container: $N8N_CONTAINER"

if [ -n "$N8N_CONTAINER" ]; then
    echo "Connecting $N8N_CONTAINER to trading-net..."
    docker network connect trading-net "$N8N_CONTAINER"
    echo "Done. Restarting n8n..."
    docker restart "$N8N_CONTAINER"
else
    echo "No n8n container found. Is n8n installed as a standalone container?"
    echo "List of running containers:"
    docker ps --format "{{.Names}}"
fi

echo -e "\n=== Verifying trading-net network ==="
docker network inspect trading-net --format '{{range .Containers}}{{println .Name "-> " .IPv4Address}}{{end}}'

echo -e "\n=== Testing service connectivity from n8n network ==="
# Test backend
echo -e "\nBackend health:"
curl -s http://ai-trading-backend:8000/health | jq . || echo "Backend unreachable"

# Test influxdb
echo -e "\nInfluxDB (should work now):"
docker run --rm --network trading-net curlimages/curl:latest \
  -X POST "http://vps-influxdb:8086/api/v2/write?org=819d45e061531bd6&bucket=news-sentiment" \
  -H "Authorization: Token YOUR_TOKEN_HERE" \
  -H "Content-Type: text/plain; charset=utf-8" \
  --data-binary "test_metric value=1" || echo "InfluxDB test failed"

# Test Qdrant
echo -e "\nQdrant collections:"
curl -s http://vps-qdrant:6333/collections | jq '.result.collections[].name' || echo "Qdrant unreachable"

echo -e "\n=== Done ==="