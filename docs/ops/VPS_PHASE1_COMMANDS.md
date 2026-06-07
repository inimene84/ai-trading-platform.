# 🚀 EXECUTE ON VPS CONSOLE

## PHASE 1 FIXES (Critical — Run Now)

### 1. Fix Qdrant Network Connectivity
```bash
docker network connect trading-net qdrant-13fq-qdrant-1
```

### 2. Check A0 Trading Loop Logs
```bash
docker logs a0-instance --tail 30
```

### 3. Verify InfluxDB Connection from n8n
n8n workflow #11 HTTP node should use:
```
URL: https://thorinvest.org/api/news/sentiment
Method: POST
Headers: Content-Type: application/json
Body: {"symbol": "{{ $json.symbol }}", "sentiment_score": "{{ $json.sentiment }}", "timestamp": "{{ Date.now() }}"}
```

### 4. Update Qdrant .env (after network fix)
```bash
# After connecting the network, find Qdrant's actual address on trading-net
docker inspect qdrant-13fq-qdrant-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'

# Should show IP like 172.x.x.x
# Then update .env:
echo 'QDRANT_URL=http://qdrant-13fq-qdrant-1:6333' >> /root/ai-trading-platform-v3/.env
```

### 5. Check if InfluxDB needs similar fix
```bash
# Check if InfluxDB is reachable from n8n's network
docker network ls | grep -i influx
docker inspect influxdb-2-ksyg-influxdb2-1 --format '{{.NetworkSettings.Networks}}'
```

---

## VERIFICATION (After Running Fixes)

```bash
# Should show: {"status":"archived","qdrant_point_id":"...","title":"Test"...}
curl -X POST http://72.60.18.113:8081/api/news/archive \
  -H "Content-Type: application/json" \
  -d '{"title":"Test","content":"Verify Qdrant","source":"validation","url":"https://test.com","published_at":"2026-06-02T00:00:00Z","sentiment":0.5,"embedding":[0.1]*1536}'
```

---

## Notes
- **Do NOT change Cloudflare** (as instructed)
- **Grafana**: Update datasource URL via Grafana UI to `http://influxdb-2-ksyg-influxdb2-1:8086` (if you can access Grafana on Hostinger network) or add nginx proxy for InfluxDB
- **Trading loop**: Check A0 logs before deciding to restart (per user preference)