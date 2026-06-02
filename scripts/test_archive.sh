#!/bin/bash
# Test archive endpoint with mock embedding to create crypto-news collection

echo "=== Testing archive endpoint (creates Qdrant collection) ==="

# Create a mock 1536-dimension embedding (all zeros for testing)
EMBEDDING='[0.0'
for i in $(seq 2 1536); do EMBEDDING+=",0.0"; done
EMBEDDING+=']'

curl -s -X POST http://localhost:8001/api/news/archive \
  -H "Content-Type: application/json" \
  -d "{
    \"title\": \"Test Article\",
    \"content\": \"This is test content for the archive\",
    \"source\": \"test\",
    \"url\": \"https://example.com/test\",
    \"published_at\": \"$(date -Iseconds)\",
    \"sentiment\": 0.5,
    \"symbols\": [\"TEST\"],
    \"embedding\": $EMBEDDING
  }" | jq .

echo -e "\n=== Verify crypto-news collection created ==="
curl -s http://localhost:6333/collections/crypto-news | jq '.result // .status'

echo -e "\n=== Done ==="