# Performance Metrics Ledger

Every performance number quoted for the Jesse research stack (`jesse-trading` repo) and the
ML gate consumed by this backend lives here, with the data range, cost assumptions, barrier
geometry, trial count and the exact code revision that produced it. Numbers that fail the
promotion gate are recorded on purpose: a metric that is not reproducible or not gated is not
a result.

Conventions

- Dates are UTC. Candle data is Binance Perpetual Futures, 1-minute candles resampled to 1h.
- "Costed" = taker fee 0.06 % per side + ATR-scaled entry slippage (base 0.03 %, cap 0.15 %,
  scaled by current ATR / mean ATR of the last 100 bars) + perpetual funding 0.01 % per 8 h on
  open notional (longs pay, shorts receive). "Zero-cost" disables all three
  (`JESSE_ZERO_COST=true` or the `storage/cost_model.json` flag written by `run_backtest.py --zero-cost`).
  `TrendFollowStrategy` does not use the cost mixin, so its costed run carries the exchange fee only.
- Barrier geometry (single source of truth `barrier_config.py`, mirrors the live backend
  `SL_ATR_MULT` / `TP_ATR_MULT`): stop 1.75 x ATR(14), take-profit 5.5 x ATR(14), trailing stop
  armed at 2.2 x ATR with a 1.6 x ATR trail, vertical barrier 48 bars for labelling.
  Theoretical payoff ratio b = 5.5 / 1.75 = 3.143 (break-even win probability 24.1 %).
- DSR = Deflated Sharpe Ratio (Bailey & Lopez de Prado) computed in per-period units with the
  cumulative trial count N read from `storage/trial_registry.json` (per symbol/timeframe scope).
  PBO = probability of backtest overfitting from CSCV (16 blocks) over the out-of-fold return
  matrix of all hyper-parameter configurations evaluated in the run.
- Gate: promote only if DSR > 0.95 and PBO < 0.30, and the served decision rule produces at
  least 20 trades on the holdout with a non-degenerate P(bull) (std >= 0.01).
  `train_ml.py --enforce-gate` exits 2 on failure; `predict_server.py` refuses artifacts whose
  recorded gate failed unless `ML_GATE_OVERRIDE=true`.

## Code revisions

| Repo | Branch | Commits |
|---|---|---|
| `jesse-trading` (VPS, `/root/jesse-trading`) | `main` (fast-forwarded from `cursor/jesse-quant-retrain-c80b`) | `aa9b034` geometry-aligned gated pipeline, true-trial DSR, cost model; `50abaa9` decision rule uses realised training payoff; `ecf0bdb` QuantumMLStrategy loads production artifacts only; `dc9dc4e` Kelly/EV reporting on NEUTRAL; `360c858` optimizer client runs inside jesse-app; `2104fb9` optimizer report keys |
| `ai-trading-platform-v3` (this repo) | `cursor/jesse-quant-retrain-c80b` | `b4e4db6` decision engine logs gate/DSR/PBO/n_trials/payoff source with the Kelly multiplier |

## 1. ML models (LightGBM, 1h, triple-barrier labels at deployed geometry)

Run date 2026-09-11. Trainer: `train_ml.py` v3 (`--labeling triple_barrier --n-configs 8 --folds 5`,
PurgedKFold with 1 % embargo, isotonic calibration inside every fold, chronological 80/20 holdout).
Decision rule on both OOF and holdout: EV_R = p*b - (1-p) >= 0.10 R with dominance 1.2, where b is
the realised avg-win / avg-loss of all training-window barrier attempts (theoretical 3.143 is
reported but not used because realised b is ~1.8-2.0 after the 48-bar vertical barrier and costs).

### Before (artifacts that were in `storage/models/` before this work)

| Symbol | Artifact | Holdout Sharpe | DSR (as recorded) | PBO | n_trials | Gate under new rules |
|---|---|---|---|---|---|---|
| BTC-USDT 1h | none (production artifact removed by an earlier retrain; only `.rejected` / `.collapsed-everybar` copies remain) | - | - | - | - | no artifact -> `/predict` error, backend fails closed |
| ETH-USDT 1h | `ETH-USDT_1h_lightgbm.joblib` (2026-09-11 02:09, legacy trainer, no geometry/trial metadata) | 0.00 | 0.4751 | 0.484 | not recorded | FAIL |
| SOL-USDT 1h | `SOL-USDT_1h_lightgbm.joblib` (2026-09-11 02:10, legacy trainer) | -2.45 | 0.0386 | 0.020 | not recorded | FAIL |

Earlier walkthroughs reported DSR = 1.0000 for these models; that value came from a DSR computed
with N = 1 trial and annualised Sharpe fed into a per-period formula. Both defects are fixed.

### After (this run; nothing promoted, all candidates kept under `storage/models/candidates/`)

Data ranges: BTC 2023-10-24 -> 2026-09-06 (25,176 bars, holdout from 2026-02-09);
ETH 2025-01-01 -> 2026-09-07 (14,738 bars, holdout from 2026-05-08);
SOL 2025-01-02 -> 2026-09-08 (14,760 bars, holdout from 2026-05-09). Costed labels.

| Symbol / label mode | Holdout trades served | Holdout return | Holdout Sharpe (ann.) | DSR (true N) | N trials | naive DSR (N=1) | PBO | b used (source) | Brier / ECE(bull) | Gate |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC two_sided | 8 / 5,036 bars | -4.24 % | -3.05 | 0.0000 | 59 | 0.0001 | 0.620 | 1.770 (training attempts) | 0.507 / 0.033 | FAIL |
| BTC event_long (QuantumAI entry events, 1,855 events) | 19 / 371 | +7.91 % | 3.78 | 0.2836 | 67 | 0.7995 | 0.652 | 1.576 (training attempts) | 0.430 / 0.048 | FAIL |
| ETH two_sided | 5 / 2,948 | -7.06 % | -3.10 | 0.0000 | 19 | 0.0009 | 0.516 | 1.911 (training attempts) | 0.491 / 0.044 | FAIL |
| ETH event_long (791 events) | 0 / 157 | 0.00 % | 0.00 | 0.0000 | 27 | 0.0000 | 0.516 | 1.896 (training attempts) | 0.370 / 0.077 | FAIL |
| SOL two_sided | 54 / 2,952 | -32.12 % | -5.13 | 0.0000 | 19 | 0.0040 | 0.320 | 1.981 (training attempts) | 0.543 / 0.055 | FAIL |
| SOL event_long (730 events) | 17 / 146 | -13.45 % | -12.42 | 0.0315 | 27 | 0.1338 | 0.560 | 1.887 (training attempts) | 0.394 / 0.096 | FAIL |

Out-of-fold CV Sharpe was negative for 6-7 of 8 configurations in every two_sided run; the
positive holdout numbers (BTC event_long) are single-path results with PBO > 0.6 and are not
evidence of an edge.

### Remedies tried on BTC two_sided (all FAIL, none promoted)

| Remedy | Window | Holdout trades | Holdout Sharpe | DSR | N | PBO | Gate |
|---|---|---|---|---|---|---|---|
| fewer trials (`--n-configs 3`) | 2023-10-24 -> 2026-09-06 | 5 | 1.98 | 0.6521 | 70 | 0.344 | FAIL |
| regularised configs only, shorter window (`--n-configs 2 --folds 4 --start 2025-01-01`) | 2025-01-02 -> 2026-09-06 | 18 | -5.53 | 0.0000 | 72 | 0.436 | FAIL |
| zero-cost labels diagnostic (`--zero-cost`, scratch model dir) | 2023-10-24 -> 2026-09-06 | 129 | 4.03 | 0.9981 | 76 | 0.524 | FAIL (PBO) |

The zero-cost diagnostic shows that whatever signal exists is smaller than the round-trip cost
(0.24 % for a 48-bar hold) and is not stable across configurations (3 of 4 OOF Sharpes <= 0).
CPCV: `purgedcv` 0.1.6 is pip-installable (pure Python; numpy/pandas/scikit-learn/scipy) but
was not integrated in this pass; it would tighten, not loosen, the verdict.

Trial registry after this run (`storage/trial_registry.json`): `ml:BTC-USDT:1h` = 76,
`ml:ETH-USDT:1h` = 27, `ml:SOL-USDT:1h` = 27, `optimizer:QuantumAIStrategy:BTC-USDT:1h` = 16 + this run.
Seeds for the pre-registry runs are documented estimates, so DSR values are upper bounds.

## 2. Strategy backtests, BTC-USDT 1h, 2023-10-23 -> 2026-09-08 (10,000 USDT, 210 warm-up bars)

Jesse 3.1.2 backtests via `run_backtest.py` (`./manage.sh backtest` / `backtest-zero-cost`), code `ecf0bdb`.

| Strategy | Cost model | Trades | Win rate | Net return | Annual return | Sharpe | Sortino | Calmar | Max DD | Fees | Funding | Slippage |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| QuantumAIStrategy | costed | 432 | 44.9 % | -5.66 % | -3.45 % | -0.45 | -0.66 | -0.20 | -17.18 % | 1,775.90 | 395.69 (1,154 events) | 568.37 |
| QuantumAIStrategy | zero-cost | 445 | 45.4 % | +9.19 % | +3.10 % | 0.46 | 0.72 | 0.27 | -11.48 % | 0 | 0 | 0 |
| TrendFollowStrategy | costed (fee only) | 1,584 | 35.0 % | -30.50 % | -11.87 % | -1.53 | -2.03 | -0.37 | -32.11 % | 3,186.56 | n/a | n/a |
| TrendFollowStrategy | zero-cost | 1,584 | 35.0 % | +1.21 % | +0.42 % | 0.09 | 0.13 | 0.04 | -10.93 % | 0 | 0 | 0 |
| QuantumMLStrategy | costed / zero-cost | 0 | - | 0.00 % | - | - | - | - | - | - | - | - |

QuantumMLStrategy executed no trades because it now loads only gate-promoted production
artifacts and no BTC artifact passes the gate; it is untestable until a model passes.
Costs (fees + funding + slippage = 2,740 USDT, 27 % of starting capital) turn QuantumAIStrategy's
small gross edge (+9.19 % over 34.5 months) into a loss. The short window previously quoted
(2024-01-01 -> 2024-03-01: 30 trades, +4.08 %, Sharpe 2.66) is a regime-local result and is not
representative.

## 3. Optimizer, QuantumAIStrategy BTC-USDT 1h

Genetic optimizer via `run_optimizer.py` (inside jesse-app), training 2023-10-23 -> 2025-12-31,
testing 2026-01-01 -> 2026-09-08, 3 trials per parameter (24 trials), 10 candidates, 2 cores.
Gate: analytic DSR with true N from the registry and IS/OOS Spearman PBO proxy.

Jesse returned 3 best candidates (fewer than the 4 needed for the IS/OOS proxy, so PBO is
"missing" and the gate fails closed). Costed (strategy cost mixin active inside the optimizer).

| Rank | Hyper-parameters (changed from deployed) | IS trades / win rate / net / Sharpe / MaxDD | OOS trades / win rate / net / Sharpe / MaxDD |
|---|---|---|---|
| #1 (trial 13, fitness 0.143) | fast_ema 12, mid_ema 40, rsi 43-51, SL 2.25x, TP 4.5x, BE 1.8, trail 2.2 | 254 / 57.5 % / +7.57 % / 0.29 / -9.84 % | 76 / 48.7 % / +0.68 % / 0.01 / -4.88 % |
| #2 (trial 22, fitness 0.096) | fast_ema 14, mid_ema 54, rsi 43-50, SL 2.5x, TP 5.0x, BE 2.4, trail 1.0 | 201 / 52.2 % / +2.72 % / 0.03 / -9.93 % | 59 / 50.8 % / -0.39 % / -0.26 / -4.23 % |
| #3 (trial 4, fitness 0.091) | fast_ema 23, mid_ema 48, rsi 44-49, SL 3.0x, TP 3.5x, BE 2.0, trail 1.6 | 170 / 61.8 % / +2.00 % / 0.00 / -11.36 % | 54 / 53.7 % / -1.19 % / -0.46 / -3.50 % |

Selected (#1) OOS Sharpe 0.011 -> DSR 0.3912 with N = 40 cumulative optimizer trials (naive N = 1
would give 0.5036). Gate: FAIL (`DSR 0.3912 <= 0.95`, `PBO missing`). No hyper-parameters were
changed in the deployed strategy. Result archived under `storage/optimizer_results/` (code `360c858`,
display fix `2104fb9`).

## 4. Monte Carlo, QuantumAIStrategy BTC-USDT 1h, 2023-10-23 -> 2026-09-08

Jesse moving-block bootstrap of the costed trade list, 500 scenarios (`./manage.sh monte-carlo QuantumAIStrategy 500`).

| Metric | 5 % (worst) | 50 % (median) | 95 % (best) |
|---|---|---|---|
| Total return | -25.46 % | -5.28 % | +13.53 % |
| Max drawdown | -29.43 % | -14.29 % | -6.96 % |
| Sharpe | -1.34 | -0.23 | 0.70 |
| Calmar | -0.36 | -0.13 | 0.57 |

## 5. Live verification (2026-09-11, predict server v3.0.0 restarted inside `jesse-app`; backend not restarted)

- `GET /health` (port 9003): `version 3.0.0`, gate thresholds `dsr_min 0.95 / pbo_max 0.30`,
  `override_active false`, deployed geometry 1.75 / 5.5 reported.
- `GET /predict?symbol=BTC-USDT&timeframe=1h` -> `status error: No model artifact found`.
- `GET /predict?symbol=ETH-USDT` -> refused: `promotion gate failed (DSR 0.4751 <= 0.95; PBO 0.484 >= 0.30)`.
- `GET /predict?symbol=SOL-USDT` -> refused: `promotion gate failed (DSR 0.0386 <= 0.95)`.
- `POST /meta-predict {BTC-USDT, BUY}` -> `VETO` (no artifact); `{ETH-USDT, BUY}` -> `VETO` (gate failed).
- Verification-only override instance (`ML_GATE_OVERRIDE=true`, scratch model dir, port 9013, stopped
  afterwards) served a candidate artifact and returned the new payload fields: `barrier_geometry`,
  `n_trials 72`, `dsr`, `pbo 0.436`, `gate {status FAIL, reasons [...]}`, `train_range`,
  `payoff_ratio 1.769 (empirical_training_attempts)`, `feature_hash cd15d2380809b247`.
- Backend `GET http://127.0.0.1:8001/health` -> `status ok, trading_status ACTIVE`.
- `./manage.sh verify-parity` -> schema 2.0.0, 26 features, hash `cd15d2380809b247`, parity verified.
- `./manage.sh run-validation` -> `test_validation_metrics.py` 10 tests OK, `test_gates.py` 11 tests OK,
  `triple_barrier.py --selftest` OK.

Consequence for live trading: with the gate enforced, the backend's LIVE-mode ML check fails closed
for BTC, ETH and SOL until a model passes DSR > 0.95 and PBO < 0.30. This is intended.
