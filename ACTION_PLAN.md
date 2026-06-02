# 🎯 FINAL ACTION PLAN - 2026-06-02

## 🔴 ROOT CAUSE
Hostinger docker containers use **different Docker networks**:
- `ai-trading-*` containers → on `trading-net` (defined in docker-compose)
- `qdrant-13fq-qdrant-1` → on Hostinger internal network (no `trading-net`)
- `n8n` → on Hostinger internal network (no `trading-net`)
- `influxdb-2-ksyg-influxdb2-1` → on `influxdb-2-ksyg_influxdb-2-ksyg_default` (external)

## ✅ COMPLETED
- [x] Backend rebuilt and healthy
- [x] News feed endpoint working (cached)
- [x] `.env` QDRANT_URL updated to correct container name
- [x] Google Drive stubs added (`/api/news/gdrive/archive`, `/api/news/gdrive/status`)

## 🚨 VPS CONSOLE - RUN THESE COMMANDS NOW

```bash
# 1. Check what network backend actually uses
docker inspect ai-trading-backend --format '{{range .NetworkSettings.Networks}}{{.NetworkSetName}}{{end}}'

# 2. If network is "trading-net" (not prefixed):
docker network connect trading-net qdrant-13fq-qdrant-1

# If network has prefix, find it:
NETWORK=$(docker inspect ai-trading-backend --format '{{range .NetworkSettings.Networks}}{{.NetworkSetName}}{{end}}')
echo "Backend network: $NETWORK"
docker network connect $NETWORK qdrant-13fq-qdrant-1

# 3. Redeploy backend to pick up .env changes
cd /root/ai-trading-platform-v3
docker compose up -d --build backend

# 4. Verify Qdrant connectivity
curl -s http://72.60.18.113:8081/api/news/gdrive/status
```

## 🌐 For n8n Workflow #11 (InfluxDB Issue)

Since n8n can't reach InfluxDB directly, add a proxy endpoint:

```bash
# Add this line to /workspace/ai-trading-platform-v3/nginx.conf:
# location /api/influxdb/ {
#     proxy_pass http://influxdb-2-ksyg-influxdb2-1:8086/;
# }

# OR have n8n POST to:
# https://thorinvest.org/api/news/sentiment
# (Backend handles InfluxDB write internally)
```

## 📡 n8n Workflow 11 URL Fix

Change the HTTP request URL in n8n workflow to:
- **URL**: `http://ai-trading-backend:8000/api/news/sentiment` (internal Docker)
- OR: `https://thorinvest.org/api/news/sentiment` (public)

---

## Expected Results After Fix
- `qdrant_point_id` will be a real ID (not null)
- `/api/news/gdrive/status` returns real collection info
- n8n can push sentiment data via backend proxy