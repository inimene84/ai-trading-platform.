# Jesse quant research scripts (VPS `/root/jesse-trading`)

These files are the promotion-gated training and serving contract for the
Jesse stack that lives on the trading VPS (separate repo `jesse-trading`).
They are mirrored here so QuantumTrade can review and deploy the same
geometry, DSR/PBO gates, and Kelly payoff as the live inference path.

## Live geometry (do not drift)

- Stop-loss: **1.75 ATR**
- Take-profit: **5.5 ATR**
- Kelly payoff fallback \(b = 5.5 / 1.75 \approx 3.14\)
- Promote only if **DSR > 0.95** and **PBO < 0.30**
- `n_trials` for DSR is the cumulative hyperparameter-grid count (ledger), not `5`

## Deploy to VPS

Copy onto the Jesse workspace (bind-mounted at `/home` in `jesse-app`):

```text
promotion_gates.py
train_ml.py
predict_server.py
triple_barrier.py
manage.sh
strategies/QuantumAIStrategy/__init__.py
```

Then:

```bash
./manage.sh auto-retrain-promote BTC-USDT 1h lightgbm
./manage.sh backtest QuantumAIStrategy
docker compose -f docker/docker-compose.yml restart jesse
```

Rejected artifacts are written as `*.rejected.joblib` and **do not** overwrite
the production model. Inference refuses models that fail the promotion gate
unless `JESSE_ML_ALLOW_OVERFIT=true`.
