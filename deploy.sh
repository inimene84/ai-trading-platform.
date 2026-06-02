#!/bin/bash
set -e

# AI Trading Platform v3 — VPS Deployment Script
# Usage: ./deploy.sh  (run from the project root on your Hostinger VPS)

echo "🚀 AI Trading Backend Deployment"
echo "=================================="

# Ensure Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker not found. Installing..."
    curl -fsSL https://get.docker.com | sh
fi

# Ensure docker-compose is available
if ! command -v docker-compose &> /dev/null; then
    echo "❌ docker-compose not found. Installing..."
    pip install docker-compose || apt-get install -y docker-compose-plugin
fi

# Check .env exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found!"
    echo "Please copy .env.example to .env and fill in your API keys."
    exit 1
fi

# Stop existing
echo "📦 Stopping existing containers..."
docker-compose -f docker-compose.prod.yml down || true

# Build and start
echo "🏗️  Building Docker image..."
docker-compose -f docker-compose.prod.yml build --no-cache

echo "🚀 Starting backend + Redis..."
docker-compose -f docker-compose.prod.yml up -d

# Wait for healthcheck
echo "⏳ Waiting for backend to be healthy..."
sleep 15
for i in {1..12}; do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ Backend is live on port 8000!"
        echo ""
        echo "🎉 Deployment Complete"
        echo "======================"
        echo "Backend: http://YOUR_VPS_IP:8000/health"
        echo "Logs:    docker-compose logs -f backend"
        echo ""
        exit 0
    fi
    echo "  ... waiting ($i/12)"
    sleep 5
done

echo "❌ Backend failed to become healthy. Check logs:"
docker-compose -f docker-compose.prod.yml logs backend --tail 50
exit 1
