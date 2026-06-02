# 🔍 DEBUG - Check port 6333

Run on VPS:
```bash
# What's using port 6333?
sudo netstat -tlnp | grep 6333
sudo lsof -i :6333

# Check if Qdrant container already exists
docker ps -a | grep qdrant

# Check existing containers on trading-net
docker network inspect trading-net --format '{{range .Containers}}{{.Name}} {{end}}'

# Kill whatever is on 6333
sudo fuser -k 6333/tcp 2>/dev/null || true
```

If the Hostinger Qdrant is somehow accessible, we might just need to use a different approach entirely.