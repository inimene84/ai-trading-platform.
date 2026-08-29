"""
Unit tests for the Layered Signal Candidate Engine, Timing Windows, and REST Routes.
"""

import pytest
import time
from starlette.testclient import TestClient
from backend.main import app
from backend.services.signal_candidate_engine import (
    signal_candidate_engine,
    TimingMode,
    CandidateStatus,
)


@pytest.fixture
def client():
    return TestClient(app)


def test_feature_computation():
    """Verify feature calculation generates valid ATR, RSI, EMAs, and trends."""
    synthetic_bars = [
        {"close": 1.0800 + (i * 0.0002), "high": 1.0805 + (i * 0.0002), "low": 1.0795 + (i * 0.0002), "volume": 100}
        for i in range(25)
    ]
    features = signal_candidate_engine._compute_features(synthetic_bars)
    assert features["last_close"] > 1.0800
    assert features["atr"] > 0
    assert 0 <= features["rsi"] <= 100
    assert features["trend"] in ["BULLISH", "BEARISH", "NEUTRAL"]
    assert features["volatility_pct"] > 0


def test_calculate_size_forex():
    """Verify position sizing uses pip-margin models on cTrader FX pairs."""
    sizing = signal_candidate_engine._calculate_size(
        symbol="EURUSD",
        entry_price=1.0850,
        stop_loss=1.0820,
        broker="ctrader"
    )
    assert "lots" in sizing
    assert sizing["lots"] >= 0.01
    assert sizing["risk_usd"] > 0
    assert sizing["stop_pips"] >= 5.0
    assert sizing["margin_required"] > 0


@pytest.mark.asyncio
async def test_scan_markets_generates_candidates():
    """Verify market scanning populates candidate signals with timing metadata."""
    universe = ["EURUSD", "GBPUSD"]
    candidates = await signal_candidate_engine.scan_markets(universe=universe, timeframe="M5")
    assert isinstance(candidates, list)
    for c in candidates:
        assert "id" in c
        assert c["symbol"] in universe
        assert c["direction"] in ["BUY", "SELL"]
        assert c["timing_mode"] in [
            TimingMode.PRE_EVENT,
            TimingMode.AT_RELEASE,
            TimingMode.POST_REACTION,
            TimingMode.BAR_CLOSE,
        ]
        assert c["earliest_exec_at"] > 0
        assert c["latest_exec_at"] >= c["earliest_exec_at"]
        assert c["sizing"]["lots"] > 0


def test_timing_window_ready_queue():
    """Verify signals inside execution window transition to READY."""
    now_ts = int(time.time())
    test_id = "test-sig-timing-01"
    signal_candidate_engine.candidates[test_id] = {
        "id": test_id,
        "symbol": "EURUSD",
        "broker": "ctrader",
        "strategy": "MOMENTUM_TREND_PULSE",
        "direction": "BUY",
        "entry_price": 1.0850,
        "stop_loss": 1.0820,
        "take_profit": 1.0910,
        "timing_mode": TimingMode.POST_REACTION,
        "status": CandidateStatus.PENDING,
        "earliest_exec_at": now_ts - 10,
        "latest_exec_at": now_ts + 300,
        "sizing": {"lots": 0.1, "quantity": 0.1, "risk_usd": 50.0},
    }

    ready = signal_candidate_engine.get_ready_signals(now_ts)
    matched = [r for r in ready if r["id"] == test_id]
    assert len(matched) == 1
    assert matched[0]["status"] == CandidateStatus.READY


def test_scan_markets_endpoint(client):
    """Test POST /api/signals/scan-markets endpoint."""
    payload = {"universe": ["EURUSD", "BTCUSDT"], "timeframe": "M5"}
    response = client.post("/api/signals/scan-markets", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "candidates" in data


def test_scan_news_endpoint(client):
    """Test POST /api/signals/scan-news endpoint."""
    payload = {"lookahead_minutes": 60}
    response = client.post("/api/signals/scan-news", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "candidates_count" in data


def test_candidates_and_ready_endpoints(client):
    """Test GET /api/signals/candidates and /ready-for-execution."""
    cands_res = client.get("/api/signals/candidates")
    assert cands_res.status_code == 200
    assert "candidates" in cands_res.json()

    ready_res = client.get("/api/signals/ready-for-execution")
    assert ready_res.status_code == 200
    assert "signals" in ready_res.json()


def test_timing_config_endpoints(client):
    """Test GET and POST /api/signals/timing-config."""
    get_res = client.get("/api/signals/timing-config")
    assert get_res.status_code == 200
    assert "config" in get_res.json()
    assert "timing_modes" in get_res.json()

    update_payload = {
        "pre_event_window_min": 20,
        "post_reaction_delay_min": 3,
        "max_spread_pips": 2.5,
        "default_risk_pct": 0.75,
        "strategies_enabled": {"momentum": True, "fade": True},
    }
    post_res = client.post("/api/signals/timing-config", json=update_payload)
    assert post_res.status_code == 200
    cfg = post_res.json()["config"]
    assert cfg["pre_event_window_min"] == 20
    assert cfg["post_reaction_delay_min"] == 3
    assert cfg["max_spread_pips"] == 2.5
    assert cfg["default_risk_pct"] == 0.75


def test_execute_candidate_endpoint_dry_run(client):
    """Test POST /api/signals/execute-candidate."""
    # Seed a ready candidate
    now_ts = int(time.time())
    sig_id = "test-exec-sig-01"
    signal_candidate_engine.candidates[sig_id] = {
        "id": sig_id,
        "symbol": "EURUSD",
        "broker": "ctrader",
        "strategy": "MOMENTUM_TREND_PULSE",
        "direction": "BUY",
        "entry_price": 1.0850,
        "stop_loss": 1.0820,
        "take_profit": 1.0910,
        "timing_mode": TimingMode.POST_REACTION,
        "status": CandidateStatus.READY,
        "earliest_exec_at": now_ts - 5,
        "latest_exec_at": now_ts + 600,
        "sizing": {"lots": 0.1, "quantity": 0.1, "risk_usd": 50.0},
    }

    res = client.post("/api/signals/execute-candidate", json={"candidate_id": sig_id, "force": True})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["candidate_id"] == sig_id


def test_signals_routes_dual_mounted_for_nginx_rewrite(client):
    """Dashboard hits /api/backend/signals/* which nginx rewrites to /signals/*."""
    get_res = client.get("/signals/timing-config")
    assert get_res.status_code == 200
    assert "config" in get_res.json()

    api_res = client.get("/api/signals/timing-config")
    assert api_res.status_code == 200
    assert api_res.json()["config"]["pre_event_window_min"] == get_res.json()["config"]["pre_event_window_min"]
