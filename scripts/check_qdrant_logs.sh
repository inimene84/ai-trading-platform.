#!/bin/bash
# Check backend logs for Qdrant errors
docker logs ai-trading-backend --tail 50 2>&1 | grep -i -E "(qdrant|archive|error)" | head -20