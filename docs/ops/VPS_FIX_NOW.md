# 🚀 VPS CONSOLE COMMANDS - RUN THESE NOW

## Step 1: Find the Actual Network Name
```bash
# Check what network the backend is on
docker inspect ai-trading-backend --format '{{json .NetworkSettings.Networks}}' | jq -r 'keys[]'
```

If that shows something like `ai-trading-platform-v3_trading-net`, use that exact name.

## Step 2: Connect Qdrant to the Backend Network
```bash
# Replace NETWORK_NAME with the actual network name from Step 1
NETWORK_NAME=$(docker inspect ai-trading-backend --format '{{range .NetworkSettings.Networks}}{{.NetworkSetName}}{{end}}')
docker network connect $NETWORK_NAME qdrant-13fq-qdrant-1 2>&1 || echo "Network connect failed, checking if network exists..."

# If network doesn't exist, it may need to be created first
docker network ls | grep -i trading
```

## Step 3: Alternative - Reconnect via docker-compose
```bash
cd /root/ai-trading-platform-v3

# This will restart backend and ensure trading-net exists
docker compose up -d --no-deps backend

# Then connect Qdrant
docker network connect trading-net qdrant-13fq-qdrant-1 2>&1 || \
docker network connect $PROJECT_PREFIX_trading-net qdrant-13fq-qdrant-1 2>&1
```

## Step 4: Redeploy Backend with Google Drive Stubs
```bash
# After network is fixed, rebuild to pick up the new code
docker compose up -d --build backend
```

## Step 5: Verify Qdrant Connectivity
```bash
# Test archive endpoint (use proper JSON)
EMB=$(python3 -c "import json; print(json.dumps([0.1]*1536))")
curl -s -X POST http://72.60.18.113:8081/api/news/archive \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Network test\",\"content\":\"Verifying Qdrant connection after network fix\",\"source\":\"network_fix\",\"url\":\"https://test.com\",\"published_at\":\"2026-06-02T00:00:00Z\",\"sentiment\":0.5,\"embedding\":$EMB}"
```

## Step 6: Check New Google Drive Endpoints
```bash
curl -s http://72.60.18.113:8081/api/news/gdrive/status
```

---

## If Network "trading-net" Doesn't Exist

Create it manually:
```bash
docker network create trading-net

# Then connect Qdrant
docker network connect trading-net qdrant-13fq-qdrant-1
docker network connect trading-net influxdb-2-ksyg-influxdb2-1

# Restart backend to join the network
docker compose up -d --no-deps backend
```