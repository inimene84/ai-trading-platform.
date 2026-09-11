# Jesse predict_server.py — add GET /model-metadata endpoint
# Apply in jesse-trading repo (already deployed on VPS via hotfix).

@app.get("/model-metadata")
def model_metadata(symbol: str = "BTC-USDT", timeframe: str = "1h", model_type: str = "lightgbm"):
    """Return MLOps metadata (DSR, PBO, holdout Sharpe) for a trained model artifact."""
    search_dirs = ["/home/storage/models", "/root/jesse-trading/storage/models", "storage/models"]
    meta_name = f"{symbol}_{timeframe}_{model_type}_meta.json"
    for sdir in search_dirs:
        meta_path = os.path.join(sdir, meta_name)
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            payload["status"] = "success"
            payload.setdefault("symbol", symbol)
            payload.setdefault("timeframe", timeframe)
            payload.setdefault("model_type", model_type)
            return payload
    return {"status": "error", "error": f"Metadata not found for {meta_name}"}
