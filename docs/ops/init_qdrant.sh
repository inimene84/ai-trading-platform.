#!/bin/bash
# Initialize Qdrant collection on startup

set -e

echo "Waiting for Qdrant to be ready..."
until curl -sf http://localhost:6333/health > /dev/null 2>&1; do
  sleep 1
done

echo "Creating crypto-news collection..."
curl -X PUT http://localhost:6333/collections/crypto-news \
  -H "Content-Type: application/json" \
  -d '{"vectors":{"size":1536,"distance":"Cosine"}}' || echo "Collection may already exist"

echo "Verifying..."
curl -s http://localhost:6333/collections | jq '.result.collections[].name'