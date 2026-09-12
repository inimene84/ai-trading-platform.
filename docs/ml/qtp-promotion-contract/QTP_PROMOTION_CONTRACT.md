# QTP Promotion Contract v1.0.0

Machine-readable contract between the **GPU training job** and the **Decision Engine**.
If these two programs cannot agree on `geometry.json`, `metrics.json`, and the
gate functions below, they are not allowed to promote a model.

- Spec version: `1.0.0`
- Date: 2026-09-12
- Library pin: `purgedcv` (eslazarev/purged-cross-validation)
- Verdicts: `REJECT` | `SHADOW` | `PROMOTE` | `ROLLBACK`
- This file is the human contract. JSON Schemas under `schema/` are authoritative for field types.

This is a system-design spec, not investment advice. It does not place orders.

## 1. Why this exists
QTP already writes DSR > 0.95 and PBO < 0.30 in the README. Those numbers are meaningless if:
- the GPU job computes DSR as a z-score and the engine treats it as a probability
- Optuna's raw trial count is used instead of `effective_n_trials`
- training barriers are 2.0 / 6.0 ATR and live barriers are 1.75 / 5.5 ATR
- the feature builder on the VPS drifted from the training matrix
- the sealed holdout was peeked during the search that produced the artifact

## 2. Artifacts the GPU job MUST write
| File | Purpose |
|---|---|
| `geometry.json` | Frozen live-strategy geometry, costs, validation design, gate thresholds |
| `feature_schema.json` | Ordered feature names, dtypes, fracdiff `d`, scaler ids |
| `metrics.json` | Every number the gates read, plus purgedcv diagnostics |
| `cpcv_paths.parquet` | One row per CPCV path (returns + path Sharpe / DD) |
| `trials_returns.parquet` | Shape `(n_obs, n_configs)` period returns for PBO |
| `model.joblib` (or equivalent) | Inference artifact |
| `env.lock` | pip/conda freeze |

The Decision Engine never recomputes DSR/PBO from raw prices. It **re-reads** `metrics.json`, verifies hashes, and applies the boolean gates. Recomputing DSR on a different return series is a different experiment.

## 3. Hash rules (train–serve parity)
All hashes are **SHA-256 hex** of UTF-8 bytes of **canonical JSON**:
`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`

| Hash | Input object | Who checks |
|---|---|---|
| `geometry_hash` | entire `geometry.json` minus the `hashes` block | engine vs. live strategy config |
| `feature_schema_hash` | entire `feature_schema.json` | engine vs. live feature builder |
| `holdout_id` | `{start,end,instrument,timeframe}` | engine refuses a second peek |

If `geometry_hash` on the VPS ≠ `geometry_hash` in `metrics.json`, verdict is `REJECT` with reason `GEOMETRY_MISMATCH`. Same for features.

## 4. purgedcv function pin
Do not use a homegrown DSR. Call:
```python
from purgedcv import (
    CombinatorialPurgedCV, PurgedKFold,
    deflated_sharpe_ratio, deflated_sharpe_ratio_full, DSRDiagnostics,
    probability_of_backtest_overfitting, PBOResult,
    effective_n_trials, path_metrics, reconstruct_paths,
    probabilistic_sharpe_ratio, minimum_backtest_length,
)
from purgedcv.optuna_integration import TrialSharpeRecorder
```

### DSR — probability, not a z-score
`deflated_sharpe_ratio(returns, n_trials, var_sharpe, *, bars_per_year=None) -> float in [0,1]`
House gate: `diag.dsr >= geometry.gates.dsr_min` (default 0.95).
`n_trials` MUST be `effective_n_trials(...)` or `TrialSharpeRecorder.n_effective()`. Using raw `study.n_trials` is `N_TRIALS_NOT_EFFECTIVE`.
`var_sharpe` from `TrialSharpeRecorder.var_sharpe(ddof=1)`.
`returns` MUST be **costed** period returns of the selected config. Zero-cost DSR under `metrics.zero_cost` is not a promotion input.

### PBO
`probability_of_backtest_overfitting(returns shape (n_configs,n_obs), n_splits=16, ...)`
House gate: `result.pbo < geometry.gates.pbo_max` (default 0.30).
PBO requires every completed trial. Winner-only matrix = `PBO_MATRIX_INCOMPLETE`.
If `n_configs < 2` or `n_obs < n_splits` → REJECT `PBO_INFEASIBLE` (do not skip PBO).

### Nested splitters
Inner: `PurgedKFold`. Outer: `CombinatorialPurgedCV` + `reconstruct_paths` + `path_metrics`.
Sealed holdout is NOT a CPCV fold; used once then `holdout.spent=true` forever for that `holdout_id`.

## 5. geometry.json locked fields (must match live)
`triple_barrier.sl_atr_mult`, `pt_atr_mult`, `atr_period`, `vertical_timeout_bars`;
`bar.timeframe` / `bar.primary_type`; `costs.*`; `gates.*`
Canonical live house geometry: SL 1.75 ATR / PT 5.5 ATR.

## 6. Gate table (deterministic order; first hard fail wins REJECT)
`SPEC_VERSION`, `SCHEMA_HASH`, `GEOMETRY_HASH`, `GEOMETRY_LIVE_LOCK`,
`N_TRIALS_NOT_EFFECTIVE`, `DSR_MISSING`, `DSR_FLOOR`,
`PBO_INFEASIBLE`, `PBO_MATRIX_INCOMPLETE`, `PBO_CEILING`,
`MIN_TRADES`, `NET_SHARPE`, `COSTED_EDGE`, `ZERO_COST_ONLY`,
`HOLDOUT_SPENT`, `HOLDOUT_FAIL`;
`PATH_LEFT_TAIL` / `CONFORMAL_COVERAGE` / `MINBTL` = warn (or optional hard per geometry).
Defaults: `dsr_min=0.95`, `pbo_max=0.30`, `min_trades=80`, `min_net_sharpe_after_costs=0.0`, `min_costed_edge_bps=0.0`, `min_path_sharpe_p10=-0.50`, `min_conformal_coverage=0.70`, `holdout_required_for_promote=true`.
Verdicts: any hard fail→`REJECT`; all hard pass + holdout unspent + `promote_requested=false`→`SHADOW`; all hard pass + holdout spent this run and pass + `promote_requested=true`→`PROMOTE`; production drift/kill→`ROLLBACK`.

## 7. Live four-number check (promoted model still gated)
`side`, `p_win`, `conformal_width`, `costed_edge_bps` — skip if `p_win < live.p_win_min` OR `conformal_width > live.width_max` OR `costed_edge_bps <= gates.min_costed_edge_bps`.

## 8. Who computes what
GPU: labels, fit, CPCV/DSR/PBO via purgedcv, `advisory_verdict`.
Engine: authoritative gates, hash recompute, holdout registry. GPU never calls `/trading/*`.

## 9. Reference evaluator
```bash
python reference/evaluate_gates.py --geometry examples/geometry.json --metrics examples/metrics.pass.json --live-geometry examples/geometry.json
```
Exit 0 `PROMOTE`/`SHADOW`, 2 `REJECT`, 3 usage/schema error.

## 10. Anti-cheating (hard fails)
No post-hoc gate threshold edits without new `strategy_id`; no dropping losing trials from PBO; no raw `n_trials` as effective; no zero-cost DSR promote; no spent holdout reuse; no train/serve ATR mismatch; no PBO on winner-only CPCV paths.
