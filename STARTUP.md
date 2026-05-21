# QuantumTrade Pro — Startup Guide

Two ways to run: **Native Linux** (fastest for dev) or **Docker** (production/VPS).

---

## OPTION 1: Native Linux (WSL / Ubuntu)

Best for development. Requires Python 3.11+, Node 18+, and a running Postgres.

### Step 1: Environment
```bash
cp .env.example .env
# Edit .env and set your API keys (XAI, BINANCE, etc.)
```

### Step 2: Backend (Terminal 1)
```bash
cd backend
pip install -r requirements.txt
cd ..
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```
Backend will be at `http://localhost:8000`

### Step 3: Frontend (Terminal 2)
```bash
cd frontend
npm install
npm run dev
```
Frontend will be at `http://localhost:3000` or `http://localhost:5173` (check console output)

### Step 4: Databases (optional, for full stack)
If you have Docker available, run just the databases:
```bash
docker compose up -d postgres influxdb grafana
```
Or connect to your existing Hostinger InfluxDB/Postgres by setting the URLs in `.env`.

---

## OPTION 2: Docker (Full Stack)

Best for production deployment on your Hostinger VPS.

### Step 1: Environment
```bash
cp .env.example .env
# Edit .env with production secrets
```

### Step 2: Build frontend first
The nginx container serves static files from `frontend/dist`:
```bash
cd frontend
npm install
npm run build
cd ..
```

### Step 3: Launch everything
```bash
docker compose up -d --build
```
This starts: Postgres, InfluxDB, Grafana, FastAPI backend, Nginx.

### Step 4: Access
- App: `http://YOUR_VPS_IP`
- API docs: `http://YOUR_VPS_IP/docs`
- Grafana: `http://YOUR_VPS_IP:3000`

### Useful commands
```bash
docker compose logs -f backend   # Watch backend logs
docker compose ps                # Check status
docker compose down              # Stop everything
docker compose up -d backend     # Restart only backend after code changes
```

---

## Quick-start script

Run `./start.sh` for native mode or `./start.sh docker` for Docker mode.
