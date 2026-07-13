#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
#  QuantumTrade Pro - Automated Local Docker Startup (Linux / macOS)
# ═══════════════════════════════════════════════════════════════════════════

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARNING]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; }

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "====================================================================="
echo "   QuantumTrade Pro - Automated Local Docker Startup"
echo "====================================================================="
echo ""

# 1. Check Docker Daemon
info "[1/5] Checking Docker status..."
if ! docker info >/dev/null 2>&1; then
    error "Docker is not running. Please start Docker / Docker Desktop first."
    exit 1
fi
success "Docker is running."
echo ""

# 2. Setup External Networks
info "[2/5] Setting up external networks..."
if ! docker network inspect trading-net >/dev/null 2>&1; then
    info "Creating missing Docker network: trading-net..."
    docker network create trading-net
else
    success "Network 'trading-net' exists."
fi

if ! docker network inspect n8n_default >/dev/null 2>&1; then
    info "Creating missing Docker network: n8n_default..."
    docker network create n8n_default
else
    success "Network 'n8n_default' exists."
fi
echo ""

# 3. Setup Environment File
info "[3/5] Checking environment configuration..."
if [[ ! -f ".env" ]]; then
    if [[ -f ".env.example" ]]; then
        info "Creating .env file from .env.example..."
        cp .env.example .env
        warn ".env has been created. Please configure your API keys in it!"
    else
        error "Neither .env nor .env.example was found."
        exit 1
    fi
else
    success ".env file is present."
fi
echo ""

# 4. Build Frontend
info "[4/5] Preparing frontend distribution..."
if [[ ! -d "frontend/dist" ]]; then
    info "Frontend distribution folder (frontend/dist) is missing."
    info "Building the frontend app..."
    
    if command -v npm >/dev/null 2>&1; then
        info "Found local npm. Building frontend locally..."
        (cd frontend && npm install && npm run build)
    else
        info "Local npm not found. Building using a Docker Node container..."
        docker run --rm -v "$PROJECT_DIR/frontend:/app" -w /app node:18-alpine sh -c "npm install && npm run build"
    fi
else
    success "Frontend distribution (frontend/dist) is already built."
    read -p "Would you like to rebuild the frontend anyway? (y/N): " rebuild
    if [[ "$rebuild" =~ ^[Yy]$ ]]; then
        if command -v npm >/dev/null 2>&1; then
            info "Rebuilding frontend locally..."
            (cd frontend && npm install && npm run build)
        else
            info "Rebuilding frontend using a Docker Node container..."
            docker run --rm -v "$PROJECT_DIR/frontend:/app" -w /app node:18-alpine sh -c "npm install && npm run build"
        fi
    fi
fi
echo ""

# 5. Start Container Stack
info "[5/5] Launching Docker Compose stack..."
if docker compose up -d --build; then
    echo ""
    echo "====================================================================="
    success "🚀 QuantumTrade Pro is running locally in Docker!"
    echo "====================================================================="
    echo ""
    echo "Access URLs:"
    echo -e "  - Web Dashboard:     ${GREEN}http://localhost:8081${NC}"
    echo -e "  - Backend API Docs:  ${GREEN}http://localhost:8001/docs${NC}"
    echo -e "  - InfluxDB Console:  ${GREEN}http://localhost:8086${NC}"
    echo -e "  - MCP Server:        ${GREEN}http://localhost:9100/mcp${NC}"
    echo ""
    echo "Useful Commands:"
    echo "  - View backend logs:   docker compose logs -f backend"
    echo "  - View all logs:       docker compose logs -f"
    echo "  - Stop the stack:      docker compose down"
    echo ""
    echo "====================================================================="
else
    error "Failed to start Docker Compose stack."
    exit 1
fi
