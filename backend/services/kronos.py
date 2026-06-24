"""
Kronos stub — minimal fallback for NeoQuasar/Kronos-mini integration.
Installs as backend.services.kronos so imports don't crash.
"""
import logging
import numpy as np

logger = logging.getLogger("kronos")

class KronosTokenizer:
    def __init__(self, *args, **kwargs):
        pass
    @classmethod
    def from_pretrained(cls, model_name, **kwargs):
        return cls()
    def encode(self, data):
        return {"input_ids": np.zeros((1, 10), dtype=np.int64)}
    def decode(self, ids):
        return "neutral"

class Kronos:
    def __init__(self, *args, **kwargs):
        pass
    @classmethod
    def from_pretrained(cls, model_name, **kwargs):
        return cls()
    def generate(self, tokens, max_new_tokens=1):
        return np.zeros((1, 1), dtype=np.int64)
    def __call__(self, input_ids):
        return type("_out", (), {"logits": np.zeros((1, 3))})()

class KronosPredictor:
    def __init__(self, model=None, tokenizer=None, device="cpu", max_context=512):
        self.model = model or Kronos()
        self.tokenizer = tokenizer or KronosTokenizer()
    def predict(self, df, x_timestamp=None, y_timestamp=None, pred_len=5, **kwargs):
        # Deterministic pseudo-prediction based on last close vs sma
        if df is not None and len(df) >= 20:
            last = float(df["close"].iloc[-1])
            sma20 = float(df["close"].iloc[-20:].mean())
            diff = (last - sma20) / sma20 if sma20 else 0
            if diff > 0.02:
                min(abs(diff)*10, 0.7)
            elif diff < -0.02:
                min(abs(diff)*10, 0.7)
            else:
                pass
            
            # Return a DataFrame mock if needed, or dict. 
            # kronos_service.py expects a DataFrame from predictor.predict
            import pandas as pd
            pred_close = last * (1 + diff)
            return pd.DataFrame({"close": [pred_close] * pred_len})
            
        import pandas as pd
        return pd.DataFrame({"close": [0.0] * pred_len})
