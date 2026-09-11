#!/usr/bin/env bash
# READ-ONLY extraction of 1h OHLCV bars from the jesse-postgres candle table.
# Aggregation happens inside Postgres so no large frame is ever materialised in
# Python on this host. Writes only to /tmp/labelstudy/.
set -euo pipefail

OUT=/tmp/labelstudy
mkdir -p "$OUT"

emit() {
  local sym="$1" maxbars="$2"
  local sql
  sql="SET statement_timeout='180s';
COPY (
  WITH bounds AS (
    SELECT max(timestamp) AS tmax FROM candle WHERE symbol='${sym}' AND timeframe='1m'
  ), src AS (
    SELECT (c.timestamp/3600000)*3600000 AS bucket, c.timestamp, c.open, c.high, c.low, c.close, c.volume
    FROM candle c, bounds b
    WHERE c.symbol='${sym}' AND c.timeframe='1m'
      AND c.timestamp >= b.tmax - ${maxbars}::bigint*3600000
  )
  SELECT bucket,
         (array_agg(open  ORDER BY timestamp ASC ))[1] AS open,
         max(high) AS high,
         min(low)  AS low,
         (array_agg(close ORDER BY timestamp DESC))[1] AS close,
         sum(volume) AS volume,
         count(*) AS n_1m
  FROM src GROUP BY bucket ORDER BY bucket
) TO STDOUT WITH CSV HEADER;"
  nice -n 19 docker exec -i jesse-postgres psql -q -U jesse_user -d jesse_db -c "$sql" > "${OUT}/${sym}_1h.csv"
  echo "${sym}: $(( $(wc -l < "${OUT}/${sym}_1h.csv") - 1 )) 1h bars -> ${OUT}/${sym}_1h.csv"
}

emit BTC-USDT 26000
emit ETH-USDT 26000
emit SOL-USDT 26000

ls -l "$OUT"
