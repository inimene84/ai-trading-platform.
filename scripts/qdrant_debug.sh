# Direct Qdrant API test commands
# Run these in the VPS shell or Qdrant dashboard

# 1. Check Qdrant health
curl http://localhost:6333/health

# 2. Create the crypto-news collection manually (1536d vectors for OpenAI)
curl -X PUT http://localhost:6333/collections/crypto-news \
  -H "Content-Type: application/json" \
  -d '{
    "vectors": {
      "size": 1536,
      "distance": "Cosine"
    }
  }'

# 3. Verify collection exists
curl http://localhost:6333/collections/crypto-news

# 4. Test archive endpoint again
curl -X POST http://localhost:8001/api/news/archive \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Article",
    "content": "Test content",
    "source": "manual",
    "url": "https://test.com",
    "published_at": "2026-06-02T14:00:00Z",
    "sentiment": 0.5,
    "symbols": ["TEST"],
    "embedding": [0.1, 0.2, 0.3]
  }'

# 5. Check backend logs for Qdrant errors
docker logs ai-trading-backend --tail 20