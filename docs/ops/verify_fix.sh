#!/bin/bash
# 🚀 Quick verification after deploy

sleep 3

echo "=== Backend health ==="
curl -s http://localhost:8000/health 2>&1 | head -20

echo ""
echo "=== Nginx test ==="
curl -s http://localhost:8081/ 2>&1 | head -5

echo ""
echo "=== Through nginx proxy ==="
curl -s http://localhost:8081/api/news/gdrive/status 2>&1 | head -20