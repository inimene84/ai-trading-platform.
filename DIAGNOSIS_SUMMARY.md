# 📊 DIAGNOSIS SUMMARY — 7 Broken Pipes

## ✅ VERIFIED WORKING
- **Backend**: Live at `http://72.60.18.113:8081`
- **News Feed**: `/api/news/feed` returns 60 items (cached)
- **Sentiment POST**: `/api/news/sentiment` stores to InfluxDB ✓
- **Fear & Greed**: Returns 23 - Extreme Fear (live data)

## 🔴 BLOCKED — Needs VPS Console Commands

### P1 + P4: Qdrant Network Separation
**Problem**: Backend can't reach Qdrant (different networks)
```bash
# RUN THIS ON VPS CONSOLE:
docker network connect trading-net qdrant-13fq-qdrant-1
```
**Evidence**: `qdrant_point_id: null` on archive endpoint

### P1 (n8n): Cross-Network Communication
**Problem**: n8n is on Hostinger network, can't reach InfluxDB directly
**Solution**: n8n workflow #11 should POST to `http://ai-trading-backend:8000/api/news/sentiment`

### P6: Trading Loop Last Run ~5.5h Ago
**Problem**: `a0-instance` last ran at 04:03 UTC
- Need to check A0 logs for errors
- Should coordinate with A0 before restarting

### P7: Grafana InfluxDB Data Source
**Problem**: Grafana on Hostinger network → InfluxDB on separate network
**Solution**: Either:
- Proxy InfluxDB through nginx (add to nginx.conf)
- Or update Grafana datasource URL in UI to nginx proxy

## 🛠️ NEXT ACTIONS

1. **On VPS Console**:
   ```bash
   docker network connect trading-net qdrant-13fq-qdrant-1
   docker logs a0-instance --tail 30
   ```

2. **Update n8n WF #11**:
   - Change InfluxDB HTTP request to: `https://thorinvest.org/api/news/sentiment`

3. **Update Grafana Datasource**:
   - URL: `http://ai-trading-nginx/api/influx-proxy` (need to add proxy)
   - Or use Hostinger's internal DNS if available

4. **Check /trading/stream error** - "name 'e' is not defined" suggests a code bug

## Endpoints Status
| Endpoint | Status | Notes |
|----------|--------|-------|
| `/api/news/feed` | ✅ Working | 60 items cached |
| `/api/news/sentiment` POST | ✅ Working | Stores to InfluxDB |
| `/api/news/archive` POST | ⚠️ Partial | Code works, but Qdrant unreachable |
| `/api/news/search` | ⚠️ Partial | Returns empty (Qdrant unreachable) |
| `/api/news/history` | ✅ Working | Returns 0 (empty but working) |
| `/trading/stream` | ❌ Error | Python: "name 'e' is not defined" |
| `/health` | ✅ Working | OK |