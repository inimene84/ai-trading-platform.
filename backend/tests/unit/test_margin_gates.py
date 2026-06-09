"""P0 safety gates: cycle margin gate + same-direction correlation cap."""
from backend.services.risk_config import RiskConfig


def _apply_cycle_gate(rc: RiskConfig, equity: float, available: float, margin_used: float):
    """Mirror of the trading_loop per-cycle margin-gate logic."""
    blocked, reason = False, ""
    if available < rc.min_available_margin_usdt:
        blocked = True
        reason = "available below floor"
    elif equity > 0 and margin_used >= equity * rc.pyramid_max_wallet_pct:
        blocked = True
        reason = "wallet pct exceeded"
    return blocked, reason


def test_gate_blocks_when_available_zero():
    rc = RiskConfig(min_available_margin_usdt=5.0, pyramid_max_wallet_pct=0.70)
    blocked, reason = _apply_cycle_gate(rc, equity=259.0, available=0.0, margin_used=40.0)
    assert blocked and "below floor" in reason


def test_gate_blocks_when_wallet_pct_exceeded():
    rc = RiskConfig(min_available_margin_usdt=5.0, pyramid_max_wallet_pct=0.70)
    # available fine, but 75% of equity locked as margin
    blocked, reason = _apply_cycle_gate(rc, equity=200.0, available=50.0, margin_used=150.0)
    assert blocked and "wallet pct" in reason


def test_gate_allows_normal_cycle():
    rc = RiskConfig(min_available_margin_usdt=5.0, pyramid_max_wallet_pct=0.70)
    blocked, _ = _apply_cycle_gate(rc, equity=200.0, available=120.0, margin_used=60.0)
    assert not blocked


def test_same_direction_cap_counts_distinct_symbols():
    rc = RiskConfig(max_same_direction_positions=5)

    class T:
        def __init__(self, symbol, direction):
            self.symbol, self.direction = symbol, direction

    all_open = [
        T("UNIUSDT", "SELL"), T("UNIUSDT", "SELL"),   # pyramid layers = 1 symbol
        T("POLUSDT", "SELL"), T("LINKUSDT", "SELL"),
        T("OPUSDT", "SELL"), T("ARBUSDT", "SELL"),
    ]
    same_dir = {t.symbol for t in all_open if t.direction == "SELL"}
    assert len(same_dir) == 5
    assert len(same_dir) >= rc.max_same_direction_positions  # 6th short blocked


def test_env_aliases_load():
    import os
    os.environ["MIN_AVAILABLE_MARGIN_USDT"] = "12.5"
    os.environ["MAX_SAME_DIRECTION_POSITIONS"] = "3"
    try:
        rc = RiskConfig()
        assert rc.min_available_margin_usdt == 12.5
        assert rc.max_same_direction_positions == 3
    finally:
        del os.environ["MIN_AVAILABLE_MARGIN_USDT"]
        del os.environ["MAX_SAME_DIRECTION_POSITIONS"]
