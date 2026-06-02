# 🚀 FINAL DEPLOY - Qdrant Local on VPS

## Issue: Port 6333 already allocated on host

## Solution: Use different host port OR internal-only

Since backend connects via Docker network (`vps-qdrant:6333`), we don't need host port mapping.

```bash
cd /root/ai-trading-platform-v3

# 1. Kill any process on 6333 (if needed)
fuser -k 6333/tcp 2>/dev/null || true

# 2. Remove old Qdrant
docker rm -f vps-qdrant 2>/dev/null || true
docker rm -f qdrant-13fq-qdrant-1 2>/dev/null || true

# 3. Pull latest
git pull

# 4. Deploy all (Qdrant internally on trading-net)
docker compose up -d

# 5. Wait for Qdrant
sleep 15

# 6. Initialize collection
curl -X PUT http://localhost:6333/collections/crypto-news \
  -H "Content-Type: application/json" \
  -d '{"vectors":{"size":1536,"distance":"Cosine"}}'

# 7. Verify
curl -s http://localhost:8081/api/news/gdrive/status | jq .
curl -s http://localhost:8081/api/health | jq .
```