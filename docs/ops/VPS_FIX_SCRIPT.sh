#!/bin/bash
# 🚀 VPS Database Network Fix Script
# Run this on the VPS to connect Qdrant to the trading-net network

set -e

echo "=== Phase 1: Fixing Database Network Connectivity ==="

# 1. Ensure trading-net exists
echo "Checking trading-net network..."
if ! docker network ls | grep -q "trading-net"; then
    echo "Creating trading-net network..."
    docker network create trading-net
else
    echo "✓ trading-net exists"
fi

# 2. Connect Qdrant to trading-net
echo "Connecting Qdrant to trading-net..."
docker network connect trading-net qdrant-13fq-qdrant-1 2>&1 || echo "Note: Qdrant may already be on this network or need different network name"

# 3. Check if we need to use the project-prefixed network
NETWORK=$(docker inspect ai-trading-backend --format '{{range .NetworkSettings.Networks}}{{.NetworkSetName}}{{end}}' 2>/dev/null || echo "")
if [ -n "$NETWORK" ] && [ "$NETWORK" != "trading-net" ]; then
    echo "Backend uses network: $NETWORK - connecting Qdrant to it..."
    docker network connect "$NETWORK" qdrant-13fq-qdrant-1 2>&1 || echo "Network connect to $NETWORK failed"
fi

# 4. Redeploy nginx with InfluxDB proxy
echo "Redeploying nginx..."
cd /root/ai-trading-platform-v3
docker compose up -d --no-deps nginx

# 5. Verify connectivity
echo ""
echo "=== Verification ==="
echo "Testing Qdrant endpoint..."
curl -s http://72.60.18.113:8081/api/news/gdrive/status | head -c 200 || echo "Qdrant check failed"

echo ""
echo "Testing InfluxDB proxy..."
curl -s http://72.60.18.113:8081/api/influxdb/health | head -c 200 || echo "InfluxDB proxy check failed"

echo ""
echo "=== Done ==="
echo "If Qdrant still shows unavailable, run: docker network ls"
echo "And manually connect: docker network connect <correct-network> qdrant-13fq-qdrant-1"