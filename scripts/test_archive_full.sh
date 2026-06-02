#!/bin/bash
# Test archive endpoint with PROPER 1536-dimension embedding

# Generate a 1536-element array (0.1 values for testing)
EMBEDDING=""
for i in $(seq 1 1536); do
    if [ $i -eq 1 ]; then
        EMBEDDING="0.1"
    else
        EMBEDDING="${EMBEDDING},0.1"
    fi
done

curl -s -X POST http://localhost:8001/api/news/archive \
  -H "Content-Type: application/json" \
  -d "{
    \"title\": \"Test News Article\",
    \"content\": \"This is a test article for the crypto news archive system\",
    \"source\": \"test-script\",
    \"url\": \"https://example.com/test-article\",
    \"published_at\": \"2026-06-02T14:00:00Z\",
    \"sentiment\": 0.5,
    \"symbols\": [\"TEST\"],
    \"embedding\": [$EMBEDDING]
  }" | jq .

echo ""
curl -s http://localhost:6333/collections/crypto-news | jq '.result.config.params.vectors, .result.points_count'