#!/usr/bin/env bash
# QuantumTrade Pro — Quick Start Script
# Usage: ./start.sh          → Native mode (backend + frontend in tmux/background)
#        ./start.sh docker   → Docker full-stack mode

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-native}"

cd "$PROJECT_DIR"

# ── Helpers ─────────────────────────────────────────────────────────
check_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: $1 not found. Install it first."; exit 1; } }

color() { printf "\033[%sm%s\033[0m\n" "$1" "$2"; }

info()  { color "34" "ℹ  $1"; }
ok()    { color "32" "✓  $1"; }
warn()  { color "33" "⚠  $1"; }
err()   { color "31" "✗  $1"; }

# ── Docker Mode ─────────────────────────────────────────────────────────────
if [ "$MODE" = "docker" ]; then
    check_cmd docker
    check_cmd docker-compose

    info "Docker mode selected"

    if [ ! -f ".env" ]; then
        warn "No .env file found. Copying from .env.example"
        cp .env.example .env
        err "EDIT .env FIRST, then re-run: ./start.sh docker"
        exit 1
    fi

    info "Building frontend..."
    cd frontend
    npm install
    npm run build
    cd ..

    info "Starting Docker stack..."
    docker compose down 2>/dev/null || true
    docker compose up -d --build

    ok "Stack running!"
    echo ""
    echo "  App:      http://localhost"
    echo "  API Docs: http://localhost/docs"
    echo "  Grafana:  http://localhost:3000"
    echo ""
    echo "Logs: docker compose logs -f backend"
    exit 0
fi

# ── Native Mode ────────────────────────────────────────────────────────────
info "Native Linux mode"
check_cmd python3
check_cmd pip
check_cmd node
check_cmd npm

if [ ! -f ".env" ]; then
    warn "No .env file found. Copying from .env.example"
    cp .env.example .env
    err "EDIT .env FIRST, then re-run: ./start.sh"
    exit 1
fi

# Install backend deps
info "Installing Python dependencies..."
pip install -q -r backend/requirements.txt
ok "Backend deps installed"

# Install frontend deps
info "Installing frontend dependencies..."
cd frontend
npm install
ok "Frontend deps installed"
cd ..

# Start backend in background
info "Starting backend on :8000 ..."
nohup uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload > backend.log 2>&1 &
BACKEND_PID=$!
ok "Backend started (PID: $BACKEND_PID) — logs: backend.log"

# Start frontend in background
info "Starting frontend dev server..."
cd frontend
nohup npm run dev > frontend.log 2>&1 &
FRONTEND_PID=$!
ok "Frontend started (PID: $FRONTEND_PID) — logs: frontend/frontend.log"
cd ..

echo ""
ok "Both services are running!"
echo ""
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:5173  (check frontend.log for exact port)"
echo "  API Docs: http://localhost:8000/docs"
echo ""
echo "Stop everything: kill $BACKEND_PID $FRONTEND_PID"
echo "Or run: pkill -f 'uvicorn backend.main' && pkill -f 'npm run dev'"
