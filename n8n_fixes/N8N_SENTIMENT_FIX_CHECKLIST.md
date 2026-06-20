# n8n Sentiment Pipeline — Diagnosis & Fix Checklist

## TL;DR — the workflow JSON was already correct

After inspecting `9.-Crypto-News-Sentiment-Feed-3.json`, the suspected bugs were **not** present:

- ✅ `Write to InfluxDB` **already** has the body binding: `body = {{ $json.lineProtocol }}`, `contentType: raw`, `text/plain`, plus a valid `Authorization: Token …` header.
- ✅ `Process News & Sentiment1` correctly builds **one item per coin** for all 20 symbols.
- ✅ `Parse & Build LineProtocol` correctly derives `BTCUSDT → BTC` and only falls back to `CRYPTO` when the symbol is genuinely missing.
- ✅ `Skip Empty` only drops items where `skip === true` (the producer sets `skip: false` for every coin).

**Conclusion:** the "stopped June 2nd / only BTC+CRYPTO tags" symptom is **operational**, not a logic bug. Work the checklist below in order.

---

## Step 1 — Is the workflow actually running? (most likely)

In the n8n UI:
1. Open workflow **9. Crypto News & Sentiment Feed** → confirm the **Active** toggle is green.
2. Check the **Every 2 h** schedule trigger node is the active trigger (not just the webhook).
3. Open **Executions** for this workflow. Look at the last successful run date.
   - If the last run is ~**June 2** → the workflow was **paused/deactivated** then. Just re-activate it.
   - If runs continue but show ⚠️/red → go to Step 2.

## Step 2 — Did the InfluxDB token / org / bucket change?

The `Write to InfluxDB` node hardcodes:
- URL: `http://vps-influxdb:8086/api/v2/write?org=819d45e061531bd6&bucket=news-sentiment&precision=ns`
- Header: `Authorization: Token Z8Kf…Dow==`

If InfluxDB was redeployed or the token rotated (common after a VPS update), **every write returns 401** and data silently stops.

Verify on the VPS:
```bash
# Does the bucket exist and is the org id right?
docker exec vps-influxdb influx bucket list

# Test the exact token the workflow uses (replace <TOKEN>):
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Token <TOKEN>" \
  --data-binary 'news_sentiment,symbol=TEST,source=manual,direction=NEUTRAL sentiment_score=0,impact_score=0,confidence=0' \
  "http://localhost:8086/api/v2/write?org=819d45e061531bd6&bucket=news-sentiment&precision=s"
# 204 = good. 401 = token wrong. 404 = org/bucket wrong.
```
If 401/404 → update the token/org/bucket in the n8n node’s URL + Authorization header.

## Step 3 — Can n8n still reach the InfluxDB container?

The node uses the Docker hostname `vps-influxdb`. If containers were renamed or the n8n
container left the shared Docker network, DNS fails and writes never connect.
```bash
docker exec <n8n-container> ping -c1 vps-influxdb   # name must resolve
docker network inspect <shared-network>             # both n8n + influx must be members
```
Fix: reconnect n8n to the network (`docker network connect <net> <n8n-container>`).

## Step 4 — Is the backend hostname correct?

`Save Sentiment to Memory1` POSTs to `http://ai-trading-backend:8000/api/news/sentiment`.
Note Hermes reported the live backend on **port 8001** (`72.60.18.113:8001`). Inside Docker
it’s `ai-trading-backend:8000`; from outside it’s `:8001`. Make sure the n8n node uses the
**internal** Docker name + port (`ai-trading-backend:8000`), not the external one.

---

## Hardening applied (optional, in `9.-Crypto-News-Sentiment-Feed-HARDENED.json`)

These don’t change logic — they stop one bad item/LLM hiccup from killing the whole batch:

| Node | Change | Why |
|------|--------|-----|
| Write to InfluxDB | `neverError=true`, `responseFormat=text`, `onError=continue` | Influx returns an empty `204` body; JSON parsing on it can throw. Text + neverError keeps the run alive. |
| Save Sentiment to Memory1 | `neverError=true`, `onError=continue` | A backend 5xx no longer aborts the cycle. |
| AI Sentiment Analyst1 | `onError=continue` | An LLM timeout for one coin no longer drops the rest. |

Import the HARDENED file as a **new** workflow first, run it once manually, confirm rows
appear for multiple coins, then disable the old one.

---

## The real fix: you no longer depend on this

A native, repo-side sentiment loop now ships in the backend
(`backend/services/sentiment_loop.py`). It writes per-coin `news_sentiment` rows directly
to InfluxDB on a schedule with no n8n, Cloudflare, or webhook in the path.

- Status: `GET  /api/news/sentiment-loop/status`
- Dry run (no writes): `POST /api/news/sentiment-loop/run?dry_run=true`
- Force a live pass: `POST /api/news/sentiment-loop/run`
- Toggle: `SENTIMENT_LOOP_ENABLED=true|false`, interval `SENTIMENT_LOOP_INTERVAL_MIN=30`

Keep n8n as a richer enrichment layer (its 8 RSS feeds are good), but the native loop is
now the reliable floor — if n8n breaks again, per-coin sentiment keeps flowing.
