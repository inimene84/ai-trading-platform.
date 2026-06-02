# Check backend logs for the actual error
docker logs ai-trading-backend --tail 100 2>&1 | grep -i -E "(error|traceback|failed|point)" | tail -30