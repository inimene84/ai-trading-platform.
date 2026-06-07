#!/bin/bash
# Quick diagnostic + deploy script for ai-trading-platform

set -e

echo "=== 1. SSH AUTH DEBUG ==="
# Check authorized_keys
echo "Current authorized_keys:"
cat /root/.ssh/authorized_keys | head -5

# Check key permissions
echo ""
echo "Key permissions:"
ls -la /root/.ssh/

# Try to restart ssh socket
echo ""
echo "Restarting ssh.socket:"
systemctl restart ssh.socket 2>/dev/null || service ssh restart 2>/dev/null || echo "SSH restart attempted"

echo ""
echo "=== 2. DOCKER STATUS ==="
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | head -20

echo ""
echo "=== 3. QDRANT CHECK ==="
# Check Qdrant collections
docker exec ai-trading-qdrant echo "Qdrant client check" 2>/dev/null || echo "Qdrant container not named ai-trading-qdrant"
curl -s http://localhost:6333/collections 2>/dev/null | jq '.result.collections[].name' || echo "Direct curl failed"

echo ""
echo "=== 4. BACKEND DEPLOY ==="
cd /root/ai-trading-platform-v3

# Pull latest changes
git pull origin main 2>/dev/null

# Deploy backend with new code
docker compose up -d --build backend

echo ""
echo "=== 5. WAIT FOR BACKEND ==="
sleep 10
curl -s http://localhost:8001/health | jq . 2>/dev/null || echo "Backend check failed"
echo ""
curl -s -o /dev/null -w "nginx /api/backend/trading/status: %{http_code}\n" \
  http://localhost:8081/api/backend/trading/status 2>/dev/null || true

echo ""
echo "Done!"