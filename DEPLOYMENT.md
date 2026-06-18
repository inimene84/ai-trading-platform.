# QuantumTrade Pro — Hostinger Deployment Architecture

## Your Current Infrastructure

```
┌───────────────────────────────────────────────────────────────────────────────────────────────┐
│                         HOSTINGER VPS                                │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │  Docker Compose Stack                                         │  │
│  │  ┌───────┐  ┌──────────────┐  ┌───────┐  ┌────────────────┐   │  │
│  │  │ Postgres │  │   InfluxDB    │  │Grafana│  │FastAPI Backend│   │  │
│  │  │  (16-alp)│  │   (2.7-alp)   │  │(10.3.0)│  │   (Python 3.11) │   │  │
│  │  │  • App DB   │  │  • Metrics    │  │• Dash  │  │  • Paper Engine  │   │  │
│  │  │  • Sessions │  │  • Signals    │  │• Alerts│  │  • AI Tool Loop  │   │  │
│  │  │  • Orders   │  │  • Prices     │  │       │  │  • DataHub     │   │  │
│  │  └───────┘  └──────────────┘  └───────┘  └────────────────┘   │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                           │                                         │
│                           │  Nginx (reverse proxy + static files)  │
│                           │  • Serves React build on /               │
│                           │  • Proxies /api/backend → FastAPI       │
│                           │  • SSE stream support (no buffering)     │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │  SSH tunnel or remote connection
                                    │
┌───────────────────────────────────────────────────────────────────────────────────────────────┐
│                    HOSTINGER WEBHOST (Remote MySQL — 25GB)              │
│  • Long-term trade archive                                            │
│  • Backup / warehouse for Postgres                                    │
│  • Analytics queries (slow, big data)                                 │
│  • Cross-region redundancy                                            │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Database Strategy

| Database | Role | Why |
|----------|------|-----|
| **Postgres** (Docker) | Primary app DB | Fast, local, ACID. Holds users, sessions, paper trades, signals, positions. |
| **InfluxDB** (Docker) | Time-series | Market prices, metrics, signal history. Optimized for high-write time data. |
| **MySQL** (Webhost 25GB) | Warehouse / Backup | Cheap bulk storage. Mirror trades nightly for long-term analytics. |

## Deploy Steps

```bash
# 1. On your VPS, clone the repo
cd /opt
 git clone <repo> quantumtrade
 cd quantumtrade

# 2. Build frontend
 cd frontend
 npm install
 npm run build
 cd ..

# 3. Create env file
 cat > .env <<EOF
 POSTGRES_PASSWORD=your_strong_password
 INFLUX_PASSWORD=your_influx_password
 GRAFANA_PASSWORD=your_grafana_password
 MYSQL_WAREHOUSE_URL=mysql://user:pass@your-webhost-db.hostinger.com:3306/warehouse
 XAI_API_KEY=...
 BINANCE_API_KEY=...
 BINANCE_API_SECRET=...
 CTRADER_ACCESS_TOKEN=...
 EOF

# 4. Start everything
 docker compose up -d

# 5. Verify
 docker compose ps
 curl http://localhost:8081/health
 curl http://localhost:8081/api/backend/trading/status
```

## Hostinger VPS quick fix (on the server)

```bash
cd /root/ai-trading-platform-v3   # or your clone path
git pull origin main
chmod +x scripts/hostinger_vps_apply.sh scripts/vps_realtime_watchdog.sh
PROJECT_DIR=$PWD ./scripts/hostinger_vps_apply.sh
```

**Nginx:** Production must rewrite `/api/backend/*` to the FastAPI paths (see `nginx.conf`). Without this, the React dashboard cannot reach `/trading/*` endpoints.

**Real-time watchdog:** Add to crontab to auto-restart unhealthy containers:

```bash
*/3 * * * * cd /root/ai-trading-platform-v3 && ./scripts/vps_realtime_watchdog.sh >> /var/log/quantumtrade-watchdog.log 2>&1
```

**Remote from Cloud Agent:** Configure secrets `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`, then run `./scripts/ssh_vps_remote.sh`.

**Canonical deploy only:** Use `hostinger_vps_apply.sh` on `main`. Older one-liners (`vps_deploy_p0_oneliner.sh`, `vps_deploy_risk_reviewer_fix.sh`) forward to it. To delete merged feature branches: `./scripts/cleanup_stale_branches.sh`.

## Nightly MySQL Backup Job

Add this cron job on your VPS to sync Postgres trades to MySQL:

```bash
# crontab -e
0 3 * * * cd /opt/quantumtrade && docker compose exec -T backend python -c "
import os, sqlalchemy as sa
pg = sa.create_engine(os.getenv('DATABASE_URL'))
mysql = sa.create_engine(os.getenv('MYSQL_WAREHOUSE_URL'))
# Mirror trades, signals, portfolio_snapshots
" >> /var/log/quantum_backup.log 2>&1
```

## Frontend Build Note

The frontend `BACKEND_URL` should be empty (uses same-origin `/api/backend`) in production:

```typescript
// frontend/src/services/apiService.ts
// localStorage BACKEND_URL not set → uses /api/backend → nginx → FastAPI
```

## Bringing to the Edge (Performance)

1. **Cloudflare** (free) in front of your VPS:
   - Caches static assets (React build)
   - DDoS protection
   - Argo Smart Routing reduces latency

2. **Grafana exposed?** Don't expose port 3000 publicly. Use the nginx `/grafana/` path or VPN-only access.

3. **SSE Keep-Alive**: The nginx config disables proxy buffering for SSE so real-time ticks reach the browser instantly.

4. **InfluxDB retention**: Set a 90-day retention on `trading` bucket to avoid disk bloat:
   ```bash
   docker compose exec influxdb influx bucket update \
     --name trading --retention 90d
   ```

5. **Use your 25GB MySQL**: Archive old trades there after 30 days in Postgres. Keeps the hot DB lean.
