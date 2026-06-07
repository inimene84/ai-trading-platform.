# 🚀 FINAL VPS DEPLOY - Run Now

## 1. Stop containers
docker compose down

## 2. Start fresh (only trading-net, no external network)
docker compose up -d

## 3. Check backend logs
docker logs ai-trading-backend --tail 30

## 4. Verify endpoints
sleep 5
curl -s http://localhost:8081/api/news/gdrive/status | jq .

## 5. Test sentiment write
curl -X POST http://localhost:8081/api/news/sentiment \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","sentiment_score":0.75}'

## 6. Check if Qdrant collection exists
curl -s http://localhost:6333/collections | jq '.result.collections[].name'