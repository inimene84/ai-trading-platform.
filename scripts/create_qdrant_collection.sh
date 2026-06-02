# Create crypto-news collection in Qdrant
# Size: 1536 for OpenAI embeddings
# Distance: Cosine for semantic similarity

curl -X PUT http://localhost:6333/collections/crypto-news \
  -H "Content-Type: application/json" \
  -d '{"vectors":{"size":1536,"distance":"Cosine"}}'

# Verify collection created
curl -s http://localhost:6333/collections/crypto-news | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Collection: {d.get(\"result\",{}).get(\"name\",\"error\")}'); print(f'Vector size: {d.get(\"result\",{}).get(\"config\",{}).get(\"params\",{}).get(\"size\",0)}'); print(f'Points: {d.get(\"result\",{}).get(\"points_count\",0)}')"