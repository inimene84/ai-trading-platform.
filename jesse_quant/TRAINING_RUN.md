# Jesse VPS training run (2026-09-11)

Artifacts stayed on the VPS (`/root/jesse-trading/storage/models/`). Joblib binaries
are not committed.

## Live stack

- Jesse dashboard/MCP/ML containers healthy; backend `trading_status=ACTIVE`
- Host: 4 vCPU, 15 GiB RAM, no GPU; Jesse Postgres holds 1,514,880 BTC 1m candles
  plus 57,600 ETH and 57,600 SOL 1m candles

## QuantumAIStrategy (1.75 ATR SL / 5.5 ATR TP + 8h funding haircut)

- Backtest BTC-USDT 1h, 2024-01-01 → 2024-04-01, $10k: **net +5.16%**
  (prior documented figure +4.06% on the old 2.0/4.0 geometry without funding)
- Monte Carlo **500** bootstrap paths, 2024-01-01 → 2024-03-01:

| Metric | 5th pct | Median | 95th pct |
|---|---|---|---|
| Total return | -2.39% | +2.66% | +8.91% |
| Max drawdown | -5.06% | -2.37% | -1.10% |
| Sharpe | -1.79 | 1.86 | 5.80 |

Percentiles now differ (true with-replacement bootstrap).

## LightGBM meta-labeler (event-sampled, live geometry)

1. Every-bar 3-class at 5.5/1.75: PBO 2.0% but **bullish recall 0%** (always SELL).
   Quarantined; would have vetoed every live BUY.
2. QuantumAI-event 3-class (1,861 setups): PBO 71.6%, bullish recall 0% — **blocked**.
3. Binary TP-before-SL meta-labeler on the same 1,861 events (656 wins / 1,204 fails):
   holdout acc 66.1%, DSR 0.9885 (n_trials=18), **PBO 46.0%**, bullish recall 3.3% —
   **blocked** by both PBO < 30% and collapsed-classifier gates.

No BTC production `*.joblib` is served. `/predict?symbol=BTC-USDT` returns
`No model artifact found` so QuantumTrade's live Jesse ML gate fail-closes instead
of trading a majority-class veto.

## Next training steps

- Collect more QuantumAI-event samples (extend candle history / lower RSI band)
- CPCV path reconstruction with `purgedcv` once N_events supports 10–20 partitions
- Shadow-log binary meta predictions until PBO < 0.30 and both-class recall ≥ 10%
