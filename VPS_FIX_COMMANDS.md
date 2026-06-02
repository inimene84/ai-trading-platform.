# VPS Fix Commands - Execute in Order

## P1-P7 Problems Summary
1. n8n on Hostinger network → can't reach InfluxDB (separate network)
2. Backend on trading-net → can't reach Qdrant (separate network)
3. LiteLLM missing DATABASE_URL (but running - check)
4. Qdrant collection `crypto-news` needs creation
5. Grafana can't reach InfluxDB (network issue)

## Commands to Run on VPS Console

### 1. Add Qdrant to trading-net (critical fix)
```bash
# Inspect Qdrant container network
docker inspect qdrant-13fq-qdrant-1 --format '{{json .NetworkSettings.Networks}}'

# Connect Qdrant to trading-net
docker network connect trading-net qdrant-13fq-qdrant-1

# Verify
docker inspect qdrant-13fq-qdrant-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
```

### 2. Create crypto-news Qdrant collection
```bash
# Create collection via API (now accessible on trading-net)
curl -X PUT http://localhost:6333/collections/crypto-news \
  -H "Content-Type: application/json" \
  -d '{"vectors":{"size":1536,"distance":"Cosine"}}'

# Verify
curl http://localhost:6333/collections | jq '.result.collections[].name'
```

### 3. Check LiteLLM status
```bash
# Check if it's actually running or just restarting
docker logs ai-trading-litellm --tail 20

# Check if DATABASE_URL is needed (depends on config)
docker inspect ai-trading-litellm --format '{{.Config.Env}}' | grep -i database || echo "No DB_URL set"
```

### 4. Update n8n workflow #11 URL
In n8n UI (hard to SSH-in):
- Change InfluxDB URL from `http://72.60.18.113:8086` to `http://ai-trading-backend:8000/api/news/sentiment`
- Or: Use public endpoint `https://thorinvest.org/api/news/sentiment`

### 5. Check A0 trading logs BEFORE restart
```bash
docker logs a0-instance --tail 30
```

### 6. Restart backend (already built, just needs network fix for Qdrant)
```bash
docker restart ai-trading-backend
```

## Alternative: Direct SSH Fix (if you can access)

If you can SSH into the VPS:

```bash
# Test the endpoints
curl -X POST https://thorinvest.org/api/news/sentiment \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","sentiment_score":0.5,"impact_score":0.3}'

# Test Qdrant via backend
curl https://thorinvest.org/api/news/history | jq '.count'
```

## Network Diagram (Fixed)
```
Before:
  Hostinger Net: [n8n] --X--> [InfluxDB]
  trading-net:   [backend] --X--> [qdrant-13fq-qdrant-1]

After:
  trading-net:   [backend] <---> [qdrant-13fq-qdrant-1]
  Hostinger Net: [n8n] --> [public HTTPS] --> [nginx:8081] --> [backend:8000]
```