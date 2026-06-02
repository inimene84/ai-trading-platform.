#!/bin/bash
# Fix port conflict and deploy Qdrant

cd /root/ai-trading-platform-v3

# Kill whatever is using port 6333
sudo fuser -k 6333/tcp 2>/dev/null || true

# Remove old Qdrant containers
docker rm -f vps-qdrant 2>/dev/null || true
docker rm -f qdrant-13fq-qdrant-1 2>/dev/null || true

# Pull latest code
git pull

# Deploy Qdrant (internal only on trading-net)
docker compose up -d qdrant
sleep 15

# Initialize collection
curl -X PUT http://localhost:6333/collections/crypto-news \
  -H "Content-Type: application/json" \
  -d '{"vectors":{"size":1536,"distance":"Cosine"}}' 2>&1

echo ""
echo "=== Verify Qdrant ==="
curl -s http://localhost:6333/collections | jq '.result.collections[].name' 2>/dev/null || curl -s http://localhost:6333/health