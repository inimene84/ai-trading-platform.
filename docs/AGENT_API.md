# Agent API — Unified Feed, Kronos Forecasts & Scheduler

Machine-consumable REST surface for external agents (e.g. Kimi, custom bots,
n8n workflows) that need market data, Kronos forecasts, or scheduler health
from QuantumTrade Pro.

All endpoints are served by the FastAPI backend and are reachable:

- **Directly** from the backend: `http://<backend-host>:8000/api/...`
- **Through the frontend reverse proxy** (nginx / dev server): `http://<frontend-host>/api/...`

Responses are JSON, fail graceful (provider outages produce per-item `error`
fields, not HTTP 500 stacks), and always include an `as_of` ISO-8601 UTC
timestamp. If the deployment has `ADMIN_API_KEY` enforcement enabled, send
`X-API-Key: <key>` with every request.

---

## 1. Unified Feed (`/api/feed`)

A `Quote` object looks like this everywhere it appears:

```json
{
  "symbol": "XAUUSD",
  "asset_class": "metal",
  "price": 2735.6,
  "change_abs": 22.3,
  "change_pct": 0.82,
  "high": 2741.0,
  "low": 2709.4,
  "volume": 185000000.0,
  "source": "yfinance",
  "as_of": "2025-01-15T12:00:00+00:00",
  "stale": false,
  "error": null
}
```

- `asset_class`: `crypto` | `stock` | `index` | `oil` | `metal` | `forex`
- `source`: `binance` | `yfinance` | `ctrader`
- On provider failure: `price` is `null`, `stale` is `true`, `error` carries the
  message. **Always check `stale`/`error` before consuming `price`.**

### GET /api/feed/overview

Quotes for the full default universe, grouped by bucket.

```bash
curl -s http://localhost:8000/api/feed/overview
```

```json
{
  "as_of": "2025-01-15T12:00:00+00:00",
  "crypto":   [ { "symbol": "BTCUSDC", "...": "..." } ],
  "equities": [ { "symbol": "AAPL",    "...": "..." } ],
  "metals":   [ { "symbol": "XAUUSD",  "...": "..." } ]
}
```

### GET /api/feed/quotes

| Param | Required | Description |
|---|---|---|
| `symbols` | no | Comma-separated list, e.g. `BTCUSDC,AAPL,XAUUSD`. Omit for the default universe. |
| `asset_class` | no | Filter by asset class (`crypto`, `stock`, `metal`, `forex`, ...). |

```bash
curl -s "http://localhost:8000/api/feed/quotes?symbols=BTCUSDC,AAPL,XAUUSD"
```

```json
{
  "as_of": "2025-01-15T12:00:00+00:00",
  "quotes": [ { "symbol": "BTCUSDC", "...": "..." } ]
}
```

### GET /api/feed/bars

OHLCV bars for one symbol (DataHub-cached ~60s).

| Param | Required | Default | Description |
|---|---|---|---|
| `symbol` | yes | — | e.g. `BTCUSDC`, `AAPL`, `XAUUSD` |
| `timeframe` | no | `1h` | e.g. `15m`, `1h`, `4h`, `1d` |
| `limit` | no | `100` | Max bars returned |

```bash
curl -s "http://localhost:8000/api/feed/bars?symbol=BTCUSDC&timeframe=1h&limit=200"
```

```json
{
  "symbol": "BTCUSDC",
  "asset_class": "crypto",
  "source": "binance",
  "data": [
    { "time": "2025-01-15T11:00:00+00:00", "open": 102400.0, "high": 102600.0, "low": 102300.0, "close": 102550.0, "volume": 1234.5 }
  ]
}
```

### GET /api/feed/scheduler/status

Health of the cron scheduler (feed refresh, snapshots, Kronos batch).

```bash
curl -s http://localhost:8000/api/feed/scheduler/status
```

```json
{
  "enabled": true,
  "jobs": [
    {
      "name": "feed_refresh",
      "cron_expr": "*/5 * * * *",
      "enabled": true,
      "last_run": "2025-01-15T11:55:00+00:00",
      "next_run": "2025-01-15T12:00:00+00:00",
      "last_error": null
    },
    { "name": "dashboard_snapshot", "cron_expr": "*/15 * * * *", "enabled": true, "last_run": null, "next_run": "2025-01-15T12:00:00+00:00", "last_error": null },
    { "name": "kronos_batch", "cron_expr": "*/30 * * * *", "enabled": true, "last_run": null, "next_run": "2025-01-15T12:00:00+00:00", "last_error": null }
  ]
}
```

---

## 2. Kronos Forecasts (`/api/forecast`)

### GET /api/forecast/{symbol}

On-demand forecast: fetches live bars from the unified feed, runs Kronos, and
returns the signal plus (optionally) the predicted path for chart overlays.

| Param | Required | Default | Description |
|---|---|---|---|
| `interval` | no | `1h` | Bar interval: `15m`, `1h`, `4h` |
| `pred_len` | no | `10` | Forecast horizon (bars ahead) |
| `include_path` | no | `false` | When `true`, include `forecast_path` |

```bash
curl -s "http://localhost:8000/api/forecast/BTCUSDC?interval=1h&pred_len=10&include_path=true"
```

```json
{
  "symbol": "BTCUSDC",
  "interval": "1h",
  "as_of": "2025-01-15T12:00:00+00:00",
  "signal": "BUY",
  "confidence": 0.72,
  "predicted_close": 103120.5,
  "predicted_change_pct": 0.56,
  "cum_change_5_pct": 0.31,
  "cum_change_10_pct": 0.56,
  "reversal_risk": false,
  "model_backend": "kronos-sidecar",
  "forecast_path": [
    { "date": "2025-01-15T13:00:00+00:00", "close": 102610.2 },
    { "date": "2025-01-15T14:00:00+00:00", "close": 102740.8 }
  ],
  "error": null
}
```

- `signal`: `BUY` | `SELL` | `NEUTRAL`
- `forecast_path` is `null` when `include_path=false` or the model hard-failed;
  on soft failures `error` is set and remaining fields hold neutral fallbacks.

### GET /api/forecast/batch/latest

Latest scheduled batch results (same shape as above, minus `forecast_path`).
Empty `results` until the scheduler's `kronos_batch` job has run once.

```bash
curl -s http://localhost:8000/api/forecast/batch/latest
```

```json
{
  "as_of": "2025-01-15T12:00:00+00:00",
  "results": [
    { "symbol": "BTCUSDC", "signal": "BUY", "cum_change_5_pct": 0.31, "...": "..." }
  ]
}
```

---

## 3. DataHub topics (push/SSE alternative to polling)

The backend publishes to these DataHub topics; they are also available on the
SSE stream (`GET /api/backend/trading/stream?topics=...`):

| Topic | TTL | Payload |
|---|---|---|
| `market:quote:{SYMBOL}` | 60s | Latest `Quote` for the symbol, e.g. `market:quote:BTCUSDC` |
| `feed:snapshot` | 20min | Full `/api/feed/overview` payload snapshot (also written to InfluxDB) |
| `forecast:{SYMBOL}` | 35min | Latest Kronos forecast for the symbol, e.g. `forecast:XAUUSD` |

---

## 4. Environment keys

| Key | Default | Purpose |
|---|---|---|
| `FEED_SYMBOLS_CRYPTO` | `BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC,XRPUSDC` | Default crypto universe |
| `FEED_SYMBOLS_EQUITIES` | `AAPL,MSFT,NVDA,SPX` | Default equities universe |
| `FEED_SYMBOLS_METALS` | `XAUUSD,XAGUSD,XPTUSD,XPDUSD` | Default metals universe |
| `FEED_SCHEDULER_ENABLED` | `true` | Master switch for the cron scheduler |
| `FEED_REFRESH_CRON` | `*/5 * * * *` | Feed warm-up job schedule |
| `DASHBOARD_SNAPSHOT_CRON` | `*/15 * * * *` | Snapshot job schedule |
| `KRONOS_BATCH_CRON` | `*/30 * * * *` | Kronos batch forecast schedule |
| `<JOB>_ENABLED` | `true` | Per-job override, e.g. `KRONOS_BATCH_ENABLED=false` |
| `KRONOS_SIDECAR_URL` | `http://kronos-infer:8001` | Kronos inference sidecar |

---

## 5. Polling guidance for external agents

1. **Quotes**: poll `GET /api/feed/quotes?symbols=...` no faster than every
   **30–60s** — quotes are TTL-cached (60s) server-side, so faster polling only
   wastes rate-limit budget. Respect the `stale` flag; a `stale=true` quote
   means the upstream provider failed and the value must not drive decisions.
2. **Forecasts**: prefer `GET /api/forecast/batch/latest` (populated every
   `KRONOS_BATCH_CRON`, default 30 min) over on-demand
   `GET /api/forecast/{symbol}` — the on-demand route runs model inference and
   is expensive. Use on-demand only for symbols outside the default universe or
   when you need `include_path=true` for charting.
3. **Freshness**: compare each payload's `as_of` against your own clock; treat
   data older than ~2× the relevant cron interval as degraded and check
   `GET /api/feed/scheduler/status` for `last_error` before alerting.
4. **Push instead of poll**: where possible subscribe to the SSE stream with
   topics `market:quote:{SYM}`, `feed:snapshot`, `forecast:{SYM}` instead of
   polling.
5. **Errors**: endpoints never 500 on provider outages — detect failure via
   per-item `error`/`stale` fields. A non-2xx HTTP status means the backend
   itself is unreachable; back off exponentially (start at 30s, cap at 10min).
