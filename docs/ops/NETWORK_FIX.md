# 🔥 CRITICAL FIX - Qdrant Network Separation

## Problem
Backend uses `QDRANT_URL=http://qdrant:6333` but:
- Container name is `qdrant-13fq-qdrant-1` (not `qdrant`)
- Qdrant is on Hostinger's separate network
- Backend is on `trading-net` network
- **Result: qdrant_point_id: null** - archive silently fails

## Solution Options

### Option A: Connect Qdrant to trading-net (Recommended)
```bash
# Run on VPS console:
docker network connect trading-net qdrant-13fq-qdrant-1

# Then update .env to use the actual IP or hostname:
# Find Qdrant's IP on trading-net:
docker inspect qdrant-13fq-qdrant-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'

# Or use the container name (it works once connected):
echo 'QDRANT_URL=http://qdrant-13fq-qdrant-1:6333' >> /root/ai-trading-platform-v3/.env.local
```

### Option B: Use Public Qdrant API
Update `.env`:
```bash
QDRANT_URL=http://72.60.18.113:6333
```
**But:** This may be blocked by firewall.

### Option C: Use Hostinger MCP Proxy Pattern
If Hostinger provides internal DNS, the backend might need to use:
- `http://qdrant-13fq-qdrant-1:6333` (full container name)
- Or Hostinger's internal DNS name

## Test After Fix
```bash
# Test Qdrant connectivity
curl -X POST http://72.60.18.113:8081/api/news/archive \
  -H "Content-Type: application/json" \
  -d '{"title":"Test","content":"Test content","source":"test","url":"https://example.com","published_at":"2026-06-02T00:00:00Z","sentiment":0.5,"embedding":[0.1]*1536}'

# Should return: {"status":"archived","qdrant_point_id":"news_...","title":"Test"...}
# NOT: qdrant_point_id: null
```

## Summary
- **Do this first:** `docker network connect trading-net qdrant-13fq-qdrant-1` on VPS
- This connects Qdrant to the same network as the backend
- Archive endpoint will then work correctly