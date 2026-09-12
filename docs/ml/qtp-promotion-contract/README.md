# QTP Promotion Contract v1.0.0

Fail-closed train→serve promotion for the Jesse / LightGBM meta-labeler.

- Human contract: [`QTP_PROMOTION_CONTRACT.md`](QTP_PROMOTION_CONTRACT.md)
- JSON Schemas (authoritative for field types): [`schema/`](schema/)
- Reference evaluator: [`reference/evaluate_gates.py`](reference/evaluate_gates.py)
- Example fixtures: [`examples/`](examples/)
- Authoritative walker used by the Decision Engine: `backend/ml/promotion_gates.py`

This contract does not place orders. The GPU training job never holds broker
credentials and never calls `/trading/*`.

## Run the reference evaluator

From this directory:

```bash
python reference/evaluate_gates.py \
  --geometry examples/geometry.json \
  --metrics examples/metrics.pass.json \
  --live-geometry examples/geometry.json \
  --feature-schema examples/feature_schema.json
```

Exit codes: `0` PROMOTE/SHADOW, `2` REJECT, `3` usage/schema error.

## House geometry lock

Training Triple-Barrier labels and live `RiskConfig` must agree:

| Field | Value |
|---|---|
| `triple_barrier.sl_atr_mult` | `1.75` |
| `triple_barrier.pt_atr_mult` | `5.5` |
| `triple_barrier.atr_period` | `14` |
| `bar.timeframe` | `1h` |

A 2.0/6.0 or 4.0/2.0 training barrier against a 1.75/5.5 live book is `GEOMETRY_LIVE_LOCK`.

## GPU extras

The Decision Engine does not import `purgedcv`. Training nodes install:

```bash
pip install -r backend/ml/requirements-train.txt
```
