# 🔧 CORRECTED VPS COMMANDS

## 1. Find actual network name for trading-net
```bash
docker network ls | grep -i trading
# If empty, the network needs to be created or re-created

# Check what network backend is on:
docker inspect ai-trading-backend --format '{{json .NetworkSettings.Networks}}' | jq -r 'keys[]'
```

## 2. Connect Qdrant to the correct network
```bash
# If network is named "trading-net":
docker network connect trading-net qdrant-13fq-qdrant-1

# If network is prefixed (e.g., "ai-trading-platform-v3_trading-net"):
docker network connect ai-trading-platform-v3_trading-net qdrant-13fq-qdrant-1

# Or connect to the network the backend is actually on:
docker network connect $(docker inspect ai-trading-backend --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' | xargs -I{} docker network ls | grep -o '[^[:space:]]$' | head -1) qdrant-13fq-qdrant-1
```

## 3. Create Qdrant collection (if not exists)
```bash
# The collection should be created on first archive call, but verify:
curl -X GET http://72.60.18.113:8081/api/news/history | jq '.count'
```

## 4. FIXED curl test (use proper JSON)
```bash
# Generate proper 1536-dim embedding array:
docker exec -it ai-trading-backend python3 -c "import json; print(json.dumps([0.1]*1536))" > /tmp/emb.json
curl -X POST http://72.60.18.113:8081/api/news/archive \
  -H "Content-Type: application/json" \
  -d '{"title":"Verify connectivity","content":"test","source":"validation","url":"https://test.com","published_at":"2026-06-02T00:00:00Z","sentiment":0.5,"embedding":'"$(cat /tmp/emb.json)"'}'
```

## 5. Check A0 logs
```bash
docker logs a0-instance --tail 30
```

---

## NETWORK CHAOS SUMMARY

- **Backend** (`ai-trading-backend`) → on `trading-net` 
- **InfluxDB** (`influxdb-2-ksyg-influxdb2-1`) → on `influxdb-2-ksyg_influxdb-2-ksyg_default`
- **Qdrant** (`qdrant-13fq-qdrant-1`) → on Hostinger's internal network (NOT trading-net)
- **n8n** → on Hostinger's internal network (NOT trading-net)

**The real issue**: Hostinger's Qdrant and n8n are on a separate network. The docker-compose's `trading-net` is defined but may not exist in the running stack.

**Quick fix**: Re-deploy docker-compose to create the network properly:
```bash
cd /root/ai-trading-platform-v3
docker compose up -d backend  # This will create trading-net if needed
docker network connect trading-net qdrant-13fq-qdrant-1
```