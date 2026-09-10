# FINAL ACTION PLAN - 2026-06-02

## ROOT CAUSE
Hostinger docker containers use **different Docker networks**:
- `ai-trading-*` containers on `trading-net` (defined in docker-compose)
- other Hostinger-managed containers may sit on a different network

## VPS CONSOLE

```bash
docker inspect ai-trading-backend --format '{{range .NetworkSettings.Networks}}{{.NetworkSetName}}{{end}}'
NETWORK=$(docker inspect ai-trading-backend --format '{{range .NetworkSettings.Networks}}{{.NetworkSetName}}{{end}}')
docker network connect "$NETWORK" qdrant-13fq-qdrant-1 || true
cd /root/ai-trading-platform-v3
docker compose up -d --build backend
curl -s http://localhost:8081/api/news/gdrive/status
```

Use the internal Docker hostname for n8n, not a public URL:
- `http://ai-trading-backend:8000/api/news/sentiment`
