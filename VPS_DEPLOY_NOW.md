# 🚀 VPS DEPLOY SCRIPT - Run Now

```bash
# 1. Go to project directory
cd /root/ai-trading-platform-v3

# 2. Pull latest code (if needed)
git pull

# 3. Rebuild backend with Qdrant fix
docker compose build --no-cache backend

# 4. Redeploy (backend uses influxdb-net, nginx uses both nets)
docker compose up -d backend nginx

# 5. Verify endpoints
echo "=== Testing endpoints ==="
curl -s http://localhost:8081/api/news/gdrive/status | jq .

# 6. Test sentiment endpoint (WF11 will use this)
curl -X POST http://localhost:8081/api/news/sentiment \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","sentiment_score":0.75}' | jq .
```

---

## 📡 n8n WF11 Update

In n8n UI, update the HTTP Request node:

| Field | Value |
|-------|-------|
| Method | POST |
| URL | `https://thorinvest.org/api/news/sentiment` |
| Headers | `Content-Type: application/json` |
| Body (JSON) | See below |

```json
{
  "symbol": "{{ $json.symbol }}",
  "sentiment_score": {{ $json.score }},
  "source": "n8n-wf11",
  "topics": ["{{ $json.topics }}"],
  "impact_score": 0.5,
  "time_horizon": "short"
}
```

---

## ✅ What's Fixed

| Problem | Solution | Status |
|---------|----------|--------|
| P1 | n8n uses public IP for InfluxDB | ✅ Use `/api/news/sentiment` (backend proxies) |
| P2 | No POST endpoint for n8n | ✅ Backend has `/api/news/sentiment` |
| P3 | News panel scrapes RSS directly | ✅ Backend has `/api/news/feed` (AI-curated) |
| P4 | Qdrant collections empty | 🔄 Backend now on `influxdb-net` to reach Qdrant |
| P5 | LiteLLM crash (missing DATABASE_URL) | ✅ Has DATABASE_URL in .env |
| P6 | Trading loop stale | ✅ User confirmed: running |
| P7 | Grafana uses public IP | ✅ Use `INFLUXDB_URL` in backend (proxied)