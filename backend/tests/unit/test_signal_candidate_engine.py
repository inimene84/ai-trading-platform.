"""
Unit tests for the Layered Signal Candidate Engine, Timing Windows, and REST Routes.
"""

import pytest
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from starlette.testclient import TestClient
from backend.main import app
from backend.services.signal_candidate_engine import (
    signal_candidate_engine,
    SignalCandidateEngine,
    TimingMode,
    CandidateStatus,
    CANDIDATE_TTL_SECONDS,
    EQUITY_FALLBACK_USD,
)
from backend.services.ctrader_service import CTraderService
from backend.services.multi_asset_bars import classify_symbol, tf_to_binance_interval


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


def test_feature_atr_clamped_when_bars_are_wrong_scale():
    """JPY-scale mismatch: ATR of ~20 on a 94.5 close must not produce a 142 TP."""
    synthetic_bars = [
        {"close": 94.54, "high": 114.54, "low": 74.54, "volume": 100}
        for _ in range(20)
    ]
    features = signal_candidate_engine._compute_features(synthetic_bars)
    assert features["atr"] <= features["last_close"] * 0.012 + 1e-9
    sl, tp = CTraderService.clamp_protective_prices(
        "NZDJPY",
        features["last_close"],
        features["last_close"] - 1.2 * features["atr"],
        features["last_close"] + 2.4 * features["atr"],
    )
    assert tp is not None and tp < 97.0
    assert sl is not None and sl > 92.0


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


def test_scan_markets_endpoint(client, auth_headers):
    """Test POST /api/signals/scan-markets endpoint."""
    payload = {"universe": ["EURUSD", "BTCUSDT"], "timeframe": "M5"}
    response = client.post("/api/signals/scan-markets", json=payload, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "candidates" in data


def test_scan_news_endpoint(client, auth_headers):
    """Test POST /api/signals/scan-news endpoint."""
    payload = {"lookahead_minutes": 60}
    response = client.post("/api/signals/scan-news", json=payload, headers=auth_headers)
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


def test_timing_config_endpoints(client, auth_headers):
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
    post_res = client.post("/api/signals/timing-config", json=update_payload, headers=auth_headers)
    assert post_res.status_code == 200
    cfg = post_res.json()["config"]
    assert cfg["pre_event_window_min"] == 20
    assert cfg["post_reaction_delay_min"] == 3
    assert cfg["max_spread_pips"] == 2.5
    assert cfg["default_risk_pct"] == 0.75


def test_execute_candidate_endpoint_dry_run(client, auth_headers):
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

    with patch("backend.services.signal_candidate_engine.ctrader_service.has_credentials", return_value=False):
        res = client.post("/api/signals/execute-candidate", json={"candidate_id": sig_id, "force": True}, headers=auth_headers)
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


def test_usdcad_classified_as_forex_not_crypto():
    """USDCAD must not match USDC substring and route to cTrader."""
    assert classify_symbol("USDCAD") == "forex"
    assert classify_symbol("BTCUSDT") == "crypto"


def test_tf_to_binance_interval_maps_m5():
    """Scanner timeframe M5 must map to Binance 5m, not invalid m5."""
    assert tf_to_binance_interval("M5") == "5m"
    assert tf_to_binance_interval("5m") == "5m"


@pytest.mark.asyncio
async def test_scan_markets_routes_usdcad_to_ctrader():
    """USDCAD should use cTrader trendbars, not Binance klines."""
    with patch.object(
        signal_candidate_engine, "_compute_features", return_value={"last_close": 1.0, "atr": 0.001, "rsi": 50, "trend": "NEUTRAL", "volatility_pct": 0.1}
    ), patch(
        "backend.services.signal_candidate_engine.ctrader_service.get_trendbars",
        return_value=[{"close": 1.36, "high": 1.361, "low": 1.359, "volume": 1}] * 25,
    ) as mock_ctrader, patch(
        "backend.services.signal_candidate_engine.binance_market_data.get_klines",
        new_callable=AsyncMock,
    ) as mock_binance:
        await signal_candidate_engine.scan_markets(universe=["USDCAD"], timeframe="M5")
        # Signal timeframe plus the slower stop-sizing timeframe, both on cTrader.
        assert mock_ctrader.call_count == 2
        requested = [c.args[1] for c in mock_ctrader.call_args_list]
        assert requested == ["M5", signal_candidate_engine.STOP_TIMEFRAME]
        mock_binance.assert_not_called()


@pytest.mark.asyncio
async def test_execute_candidate_accepts_ctrader_sent_status():
    """cTrader live dispatch returns status=sent before fill ack; must count as success."""
    now_ts = int(time.time())
    sig_id = "test-exec-sent-01"
    signal_candidate_engine.candidates[sig_id] = {
        "id": sig_id,
        "symbol": "EURUSD",
        "broker": "ctrader",
        "strategy": "MOMENTUM_TREND_PULSE",
        "direction": "BUY",
        "entry_price": 1.0850,
        "stop_loss": 1.0820,
        "take_profit": 1.0910,
        "timing_mode": TimingMode.BAR_CLOSE,
        "status": CandidateStatus.READY,
        "earliest_exec_at": now_ts - 5,
        "latest_exec_at": now_ts + 600,
        "sizing": {"lots": 0.01, "quantity": 0.01, "risk_usd": 50.0},
    }

    with patch.object(signal_candidate_engine, "_open_ctrader_position_count", return_value=0), patch(
        "backend.services.signal_candidate_engine.ctrader_service.ensure_connected",
        return_value=True,
    ), patch(
        "backend.services.signal_candidate_engine.ctrader_service.place_order",
        return_value={"status": "sent", "symbol": "EURUSD", "direction": "BUY", "quantity": 0.01},
    ), patch(
        "backend.services.signal_candidate_engine.persist_ctrader_execution",
        return_value=99,
    ) as mock_persist:
        res = await signal_candidate_engine.execute_candidate(sig_id, force=True)

    assert res["success"] is True
    assert signal_candidate_engine.candidates[sig_id]["status"] == CandidateStatus.EXECUTED
    mock_persist.assert_called_once()
    assert mock_persist.call_args.kwargs["symbol"] == "EURUSD"
    assert mock_persist.call_args.kwargs["direction"] == "BUY"


@pytest.mark.asyncio
async def test_execute_candidate_rejects_simulated_when_credentials_exist():
    """Paper simulated fills must not masquerade as live cTrader execution."""
    now_ts = int(time.time())
    sig_id = "test-exec-simulated-01"
    signal_candidate_engine.candidates[sig_id] = {
        "id": sig_id,
        "symbol": "EURUSD",
        "broker": "ctrader",
        "strategy": "MOMENTUM_TREND_PULSE",
        "direction": "BUY",
        "entry_price": 1.0850,
        "stop_loss": 1.0820,
        "take_profit": 1.0910,
        "timing_mode": TimingMode.BAR_CLOSE,
        "status": CandidateStatus.READY,
        "earliest_exec_at": now_ts - 5,
        "latest_exec_at": now_ts + 600,
        "sizing": {"lots": 0.01, "quantity": 0.01, "risk_usd": 50.0},
    }

    with patch.object(signal_candidate_engine, "_open_ctrader_position_count", return_value=0), patch(
        "backend.services.signal_candidate_engine.ctrader_service.ensure_connected",
        return_value=False,
    ), patch(
        "backend.services.signal_candidate_engine.ctrader_service.has_credentials",
        return_value=True,
    ), patch(
        "backend.services.signal_candidate_engine.persist_ctrader_execution",
    ) as mock_persist:
        res = await signal_candidate_engine.execute_candidate(sig_id, force=True)

    assert res["success"] is False
    assert "not connected" in res["error"].lower()
    assert signal_candidate_engine.candidates[sig_id]["status"] == CandidateStatus.READY
    mock_persist.assert_not_called()


@pytest.mark.asyncio
async def test_execute_candidate_skips_market_closed_without_blocking_queue():
    now_ts = int(time.time())
    sig_id = "test-exec-closed-01"
    signal_candidate_engine.candidates[sig_id] = {
        "id": sig_id,
        "symbol": "XAUUSD",
        "broker": "ctrader",
        "strategy": "MACRO_EVENT_POST_REACTION",
        "direction": "BUY",
        "entry_price": 2500.0,
        "stop_loss": 2490.0,
        "take_profit": 2520.0,
        "timing_mode": TimingMode.BAR_CLOSE,
        "status": CandidateStatus.READY,
        "earliest_exec_at": now_ts - 5,
        "latest_exec_at": now_ts + 600,
        "sizing": {"lots": 0.01, "quantity": 0.01, "risk_usd": 50.0},
    }

    with patch.object(signal_candidate_engine, "_open_ctrader_position_count", return_value=0), patch.dict(
        signal_candidate_engine.execution_config, {"forex_only": False, "include_metals": True}
    ), patch(
        "backend.services.signal_candidate_engine.ctrader_service.ensure_connected",
        return_value=True,
    ), patch(
        "backend.services.signal_candidate_engine.ctrader_service.place_order",
        return_value={"status": "error", "error": "MARKET_CLOSED — Trading is not available: Market is closed."},
    ):
        res = await signal_candidate_engine.execute_candidate(sig_id, force=True)

    assert res["success"] is False
    assert res["skipped"] is True
    assert signal_candidate_engine.candidates[sig_id]["status"] == CandidateStatus.CANCELLED


def test_get_ready_signals_forex_only_excludes_crypto():
    now_ts = int(time.time())
    signal_candidate_engine.candidates["fx-ready"] = {
        "id": "fx-ready",
        "symbol": "EURUSD",
        "broker": "ctrader",
        "status": CandidateStatus.READY,
        "confidence": 0.9,
        "earliest_exec_at": now_ts - 5,
        "latest_exec_at": now_ts + 600,
    }
    signal_candidate_engine.candidates["cr-ready"] = {
        "id": "cr-ready",
        "symbol": "BTCUSDT",
        "broker": "binance_futures",
        "status": CandidateStatus.READY,
        "confidence": 0.99,
        "earliest_exec_at": now_ts - 5,
        "latest_exec_at": now_ts + 600,
    }

    with patch.object(signal_candidate_engine, "_open_ctrader_position_count", return_value=0):
        ready = signal_candidate_engine.get_ready_signals(now_ts, forex_only=True, limit=5)

    ids = {c["id"] for c in ready}
    assert "fx-ready" in ids
    assert "cr-ready" not in ids


def test_get_ready_signals_forex_only_excludes_metals_by_default():
    now_ts = int(time.time())
    previous = dict(signal_candidate_engine.candidates)
    signal_candidate_engine.candidates.clear()
    try:
        signal_candidate_engine.candidates["fx-eur"] = {
            "id": "fx-eur",
            "symbol": "EURUSD",
            "broker": "ctrader",
            "status": CandidateStatus.READY,
            "confidence": 0.8,
            "earliest_exec_at": now_ts - 5,
            "latest_exec_at": now_ts + 600,
        }
        signal_candidate_engine.candidates["mt-gold"] = {
            "id": "mt-gold",
            "symbol": "XAUUSD",
            "broker": "ctrader",
            "status": CandidateStatus.READY,
            "confidence": 0.99,
            "earliest_exec_at": now_ts - 5,
            "latest_exec_at": now_ts + 600,
        }

        with patch.dict(
            signal_candidate_engine.execution_config,
            {"include_metals": False, "forex_only": True, "max_open_ctrader_positions": 10},
        ), patch.object(
            signal_candidate_engine, "_open_ctrader_position_count", return_value=0
        ), patch.object(
            signal_candidate_engine, "_open_ctrader_symbols", return_value=set()
        ):
            ready = signal_candidate_engine.get_ready_signals(now_ts, forex_only=True, limit=10)

        ids = {c["id"] for c in ready}
        assert "fx-eur" in ids
        assert "mt-gold" not in ids
    finally:
        signal_candidate_engine.candidates = previous


def test_get_ready_signals_empty_when_ctrader_position_cap_reached():
    now_ts = int(time.time())
    signal_candidate_engine.candidates["fx-cap"] = {
        "id": "fx-cap",
        "symbol": "GBPUSD",
        "broker": "ctrader",
        "status": CandidateStatus.READY,
        "confidence": 0.8,
        "earliest_exec_at": now_ts - 5,
        "latest_exec_at": now_ts + 600,
    }

    with patch.dict(signal_candidate_engine.execution_config, {"max_open_ctrader_positions": 1}), patch.object(
        signal_candidate_engine, "_open_ctrader_position_count", return_value=1
    ):
        ready = signal_candidate_engine.get_ready_signals(now_ts, forex_only=True, limit=1)

    assert ready == []


def test_get_ready_signals_allows_multiple_when_slots_remain():
    now_ts = int(time.time())
    for i, sym in enumerate(["EURUSD", "GBPUSD", "USDJPY"]):
        signal_candidate_engine.candidates[f"fx-multi-{i}"] = {
            "id": f"fx-multi-{i}",
            "symbol": sym,
            "broker": "ctrader",
            "status": CandidateStatus.READY,
            "confidence": 0.9 - (i * 0.01),
            "earliest_exec_at": now_ts - 5,
            "latest_exec_at": now_ts + 600,
        }

    with patch.dict(
        signal_candidate_engine.execution_config,
        {"max_open_ctrader_positions": 5, "max_ready_per_poll": 2, "one_position_per_symbol": True},
    ), patch.object(
        signal_candidate_engine, "_open_ctrader_position_count", return_value=1
    ), patch.object(
        signal_candidate_engine, "_open_ctrader_symbols", return_value={"EURUSD"}
    ):
        ready = signal_candidate_engine.get_ready_signals(now_ts, forex_only=True, limit=2)

    symbols = [c["symbol"] for c in ready]
    assert "EURUSD" not in symbols
    assert len(ready) == 2
    assert set(symbols) <= {"GBPUSD", "USDJPY"}


def test_get_ready_signals_fills_up_to_ten_open_slots():
    now_ts = int(time.time())
    previous = dict(signal_candidate_engine.candidates)
    signal_candidate_engine.candidates.clear()
    pairs = [
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
        "USDCHF", "NZDUSD", "EURGBP", "EURJPY", "GBPJPY", "AUDJPY",
    ]
    try:
        for i, sym in enumerate(pairs):
            signal_candidate_engine.candidates[f"fx-ten-{i}"] = {
                "id": f"fx-ten-{i}",
                "symbol": sym,
                "broker": "ctrader",
                "status": CandidateStatus.READY,
                "confidence": 0.95 - (i * 0.01),
                "earliest_exec_at": now_ts - 5,
                "latest_exec_at": now_ts + 600,
            }

        with patch.dict(
            signal_candidate_engine.execution_config,
            {"max_open_ctrader_positions": 10, "max_ready_per_poll": 10, "one_position_per_symbol": True},
        ), patch.object(
            signal_candidate_engine, "_open_ctrader_position_count", return_value=0
        ), patch.object(
            signal_candidate_engine, "_open_ctrader_symbols", return_value=set()
        ):
            ready = signal_candidate_engine.get_ready_signals(now_ts, forex_only=True)

        symbols = [c["symbol"] for c in ready]
        assert len(ready) == 10
        assert len(set(symbols)) == 10
        assert "AUDJPY" not in symbols  # 11th pair is above the cap
    finally:
        signal_candidate_engine.candidates = previous


@pytest.mark.asyncio
async def test_execute_candidate_keeps_ready_on_paper_failure():
    """Failed paper fills must not mark candidate EXECUTED."""
    now_ts = int(time.time())
    sig_id = "test-exec-fail-01"
    signal_candidate_engine.candidates[sig_id] = {
        "id": sig_id,
        "symbol": "BTCUSDT",
        "broker": "binance_futures",
        "strategy": "MOMENTUM_TREND_PULSE",
        "direction": "BUY",
        "entry_price": 0,
        "stop_loss": 90000,
        "take_profit": 110000,
        "timing_mode": TimingMode.BAR_CLOSE,
        "status": CandidateStatus.READY,
        "earliest_exec_at": now_ts - 5,
        "latest_exec_at": now_ts + 600,
        "sizing": {"lots": 0.1, "quantity": 0.01, "risk_usd": 50.0},
    }

    mock_resp = MagicMock(success=False, order_id="paper_000001", message="Paper market entry requires an explicit price or prior market fill")
    with patch.dict(signal_candidate_engine.execution_config, {"forex_only": False}), patch.object(
        signal_candidate_engine, "_resolve_mark_price", new_callable=AsyncMock, return_value=0.0
    ), patch(
        "backend.services.signal_candidate_engine.UnifiedTrading"
    ) as mock_ut_cls:
        mock_ut_cls.return_value.place_order.return_value = mock_resp
        res = await signal_candidate_engine.execute_candidate(sig_id, force=True)

    assert res["success"] is False
    assert signal_candidate_engine.candidates[sig_id]["status"] == CandidateStatus.READY
    assert signal_candidate_engine.candidates[sig_id]["execution_result"]["success"] is False


def test_resolve_equity_uses_live_broker_and_logs_fallback():
    engine = SignalCandidateEngine()
    with patch(
        "backend.services.signal_candidate_engine.ctrader_service.get_balance",
        return_value={"equity": 40000.0, "balance": 40000.0},
    ):
        assert engine._resolve_equity("ctrader") == pytest.approx(40000.0)
        # Cached — second call must not re-hit the broker.
        with patch(
            "backend.services.signal_candidate_engine.ctrader_service.get_balance",
            side_effect=AssertionError("should use cache"),
        ):
            assert engine._resolve_equity("ctrader") == pytest.approx(40000.0)

    engine._equity_cache.clear()
    with patch(
        "backend.services.signal_candidate_engine.binance_futures_broker.get_balance",
        return_value={"equity": 0.0, "balance": 0.0},
    ):
        assert engine._resolve_equity("binance_futures") == pytest.approx(EQUITY_FALLBACK_USD)


def test_calculate_size_scales_with_live_equity():
    engine = SignalCandidateEngine()
    with patch(
        "backend.services.signal_candidate_engine.ctrader_service.get_balance",
        return_value={"equity": 10000.0},
    ):
        small = engine._calculate_size("EURUSD", 1.0850, 1.0820, "ctrader")
    engine._equity_cache.clear()
    with patch(
        "backend.services.signal_candidate_engine.ctrader_service.get_balance",
        return_value={"equity": 50000.0},
    ):
        large = engine._calculate_size("EURUSD", 1.0850, 1.0820, "ctrader")
    assert large["risk_usd"] == pytest.approx(small["risk_usd"] * 5.0)
    assert large["lots"] > small["lots"]


def test_calculate_size_crypto_uses_candidate_stop_not_hardcoded_pct():
    engine = SignalCandidateEngine()
    with patch(
        "backend.services.signal_candidate_engine.binance_futures_broker.get_balance",
        return_value={"equity": 10000.0},
    ):
        tight = engine._calculate_size("BTCUSDT", 100.0, 99.8, "binance_futures")
        wide = engine._calculate_size("BTCUSDT", 100.0, 96.0, "binance_futures")
    # Same $50 risk: tighter stop → larger qty (sized from the stop, not 0.4%).
    assert tight["quantity"] > wide["quantity"]
    assert tight["risk_usd"] == pytest.approx(50.0)
    assert wide["risk_usd"] == pytest.approx(50.0)


def test_prune_terminal_candidates_drops_old_terminal_keeps_live():
    engine = SignalCandidateEngine()
    now = int(time.time())
    old_iso = datetime.fromtimestamp(now - CANDIDATE_TTL_SECONDS - 60, tz=timezone.utc).isoformat()
    fresh_iso = datetime.fromtimestamp(now - 60, tz=timezone.utc).isoformat()
    engine.candidates = {
        "old-exec": {
            "status": CandidateStatus.EXECUTED,
            "created_at": old_iso,
            "latest_exec_at": now - CANDIDATE_TTL_SECONDS - 10,
        },
        "fresh-exp": {
            "status": CandidateStatus.EXPIRED,
            "created_at": fresh_iso,
            "latest_exec_at": now - 10,
        },
        "live": {
            "status": CandidateStatus.READY,
            "created_at": old_iso,
            "earliest_exec_at": now - 10,
            "latest_exec_at": now + 600,
        },
    }
    removed = engine._prune_terminal_candidates(now)
    assert removed == 1
    assert "old-exec" not in engine.candidates
    assert "fresh-exp" in engine.candidates
    assert "live" in engine.candidates


def test_get_ready_signals_prunes_stale_terminal_store():
    engine = SignalCandidateEngine()
    now = int(time.time())
    old_iso = datetime.fromtimestamp(now - CANDIDATE_TTL_SECONDS - 5, tz=timezone.utc).isoformat()
    engine.candidates["stale"] = {
        "status": CandidateStatus.CANCELLED,
        "created_at": old_iso,
        "latest_exec_at": now - CANDIDATE_TTL_SECONDS,
        "broker": "ctrader",
        "symbol": "EURUSD",
        "confidence": 0.9,
        "earliest_exec_at": now - CANDIDATE_TTL_SECONDS - 100,
    }
    with patch.object(engine, "_open_ctrader_symbols", return_value=set()), \
         patch.object(engine, "_ctrader_execution_slots_remaining", return_value=10):
        engine.get_ready_signals(current_ts=now, enforce_ctrader_position_cap=False)
    assert "stale" not in engine.candidates
