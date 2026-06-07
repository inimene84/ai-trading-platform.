#!/bin/bash
# 🚀 COMPLETE QDRANT FIX & DEPLOY SCRIPT
# Run this on the VPS to resolve port 6333 conflict and deploy Qdrant

set -e

echo "=============================================="
echo "🔍 STEP 1 - Diagnose port 6333 conflict"
echo "=============================================="

# Check what's using port 6333
if sudo lsof -i :6333 >/dev/null 2>&1; then
    echo "Found process on port 6333:"
    sudo lsof -i :6333
else
    echo "No process found on port 6333 via lsof"
fi

# Check Docker containers with port 6333
echo ""
echo "Docker containers (any with 6333):"
docker ps -a --format "table {{.Names}}\t{{.Ports}}" | grep -E "6333|qdrant" || echo "No Qdrant containers found"

# Check all Qdrant containers
echo ""
echo "All Qdrant images/containers:"
docker ps -a --filter "ancestor=qdrant" --filter "name=qdrant" --format "{{.Names}}" 2>/dev/null || echo "None"

echo ""
echo "=============================================="
echo "🧹 STEP 2 - Clean up old containers"
echo "=============================================="

# Kill the process on port 6333
sudo fuser -k 6333/tcp 2>/dev/null && echo "Killed process on 6333" || echo "No process to kill on 6333"

# Remove old Qdrant containers (any name with qdrant)
for container in $(docker ps -aq --filter "name=qdrant" 2>/dev/null); do
    echo "Removing old container: $container"
    docker rm -f "$container" 2>/dev/null || true
done

# Remove any containers by Qdrant image
for container in $(docker ps -aq --filter "ancestor=qdrant/qdrant" 2>/dev/null); do
    echo "Removing Qdrant image container: $container"
    docker rm -f "$container" 2>/dev/null || true
done

echo ""
echo "=============================================="
echo "🔄 STEP 3 - Deploy fresh Qdrant"
echo "=============================================="

cd /root/ai-trading-platform-v3

# Create storage directory
mkdir -p ./qdrant-storage
chmod 777 ./qdrant-storage

# Pull latest code
git pull origin main 2>/dev/null || git pull 2>/dev/null || echo "Git pull failed, continuing..."

# Remove any old docker-compose artifacts
docker compose down --remove-orphans 2>/dev/null || true

# Start Qdrant (it runs on internal trading-net only)
docker compose up -d qdrant

echo ""
echo "=============================================="
echo "⏱️ STEP 4 - Wait for startup"
echo "=============================================="

sleep 15

echo ""
echo "=============================================="
echo "✅ STEP 5 - Verify deployment"
echo "=============================================="

echo "Container status:"
docker ps | grep -E "qdrant|backend" || true

echo ""
echo "Qdrant logs (last 20 lines):"
docker logs vps-qdrant --tail 20 2>/dev/null || echo "Could not get Qdrant logs"

echo ""
echo "Backend logs (last 20 lines):"
docker logs ai-trading-backend --tail 20 2>/dev/null || echo "Could not get backend logs"

echo ""
echo "=============================================="
echo "🎯 STEP 6 - Test endpoints"
echo "=============================================="

echo "Health check:"
curl -s http://localhost:8081/health 2>/dev/null | jq . || echo "Health check failed"

echo ""
echo "GDrive status (tests Qdrant connectivity):"
curl -s http://localhost:8081/api/news/gdrive/status 2>/dev/null | jq . || echo "GDrive status check failed"

echo ""
echo "Sentiment endpoint test:"
curl -s -X POST http://localhost:8081/api/news/sentiment \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","sentiment_score":0.75}' 2>/dev/null | jq . || echo "Sentiment check failed"

echo ""
echo "=============================================="
echo "🏁 Done! Check output above for any errors."
echo "=============================================="