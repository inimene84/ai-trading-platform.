#!/bin/bash
# 🚀 VPS FULL FIX - Run on root@srv1071801

set -e

echo "=== STEP 1: Rebuild backend with Qdrant fix ==="
cd /root/ai-trading-platform-v3
docker compose build --no-cache backend

echo "=== STEP 2: Ensure trading-net exists ==="
docker network create trading-net 2>/dev/null || echo "Network trading-net already exists"

echo "=== STEP 3: Connect Qdrant to trading-net ==="
docker inspect qdrant-13fq-qdrant-1 >/dev/null 2>&1 && {
    docker network connect trading-net qdrant-13fq-qdrant-1 2>&1 || echo "Qdrant already connected or on different network"
}

echo "=== STEP 4: Get actual network name (in case of Docker prefix) ==="
ACTUAL_NETWORK=$(docker inspect ai-trading-backend --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' | head -1)
echo "Backend network: $ACTUAL_NETWORK"

if [ "$ACTUAL_NETWORK" != "trading-net" ] && [ -n "$ACTUAL_NETWORK" ]; then
    echo "Connecting Qdrant to actual network: $ACTUAL_NETWORK"
    docker network connect "$ACTUAL_NETWORK" qdrant-13fq-qdrant-1 2>&1 || echo "Already connected"
fi

echo "=== STEP 5: Restart nginx with updated config ==="
docker compose up -d --no-deps nginx

echo "=== STEP 6: Verify endpoints ==="
echo "Testing InfluxDB proxy..."
curl -s http://localhost:80/api/influxdb/health | python3 -c "import sys,json; d=json.load(sys.stdin); print('InfluxDB OK:', d.get('health',{}).get('status','unknown'))" 2>/dev/null || echo "InfluxDB proxy failed"

echo "Testing GDrive status..."
curl -s http://localhost:80/api/news/gdrive/status | python3 -c "import sys,json; d=json.load(sys.stdin); print('Qdrant status:', d)" 2>/dev/null || echo "Qdrant check failed"

echo "=== DONE ==="
echo "If issues persist, check: docker logs ai-trading-nginx --tail 10"