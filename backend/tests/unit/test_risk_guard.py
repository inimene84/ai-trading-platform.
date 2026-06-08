import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone, timedelta

from backend.database.models import Base, Trade, PortfolioSnapshot
from backend.services.risk_config import RiskConfig
from backend.services.risk_guard import enforce_risk_limits, RiskBreach

@pytest.fixture(autouse=True)
def disable_risk_guard_override(monkeypatch):
    monkeypatch.setenv("DISABLE_RISK_GUARD", "false")

@pytest.fixture
def db_session():
    # Setup clean in-memory SQLite database for testing queries
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()

def test_risk_guard_allows_safe_portfolio(db_session):
    cfg = RiskConfig(
        max_position_risk_pct=1.0,
        max_portfolio_drawdown_pct=20.0,
        max_daily_loss_pct=5.0,
        max_open_positions=10,
        sl_cooldown_minutes=30
    )
    
    # Standard snapshot inside limits
    snap = PortfolioSnapshot(
        total_value=10000.0,
        cash=10000.0,
        timestamp=datetime.now(timezone.utc)
    )
    db_session.add(snap)
    db_session.commit()
    
    # Should run cleanly without raising exception
    enforce_risk_limits(db_session, cfg, [], snap)

def test_risk_guard_blocks_excessive_positions(db_session):
    cfg = RiskConfig(
        max_position_risk_pct=1.0,
        max_portfolio_drawdown_pct=20.0,
        max_daily_loss_pct=5.0,
        max_open_positions=2,  # set low limit
        sl_cooldown_minutes=30
    )
    
    trades = [
        Trade(symbol="BTCUSDT", direction="BUY", quantity=0.1, status="open"),
        Trade(symbol="ETHUSDT", direction="BUY", quantity=1.0, status="open"),
        Trade(symbol="SOLUSDT", direction="BUY", quantity=5.0, status="open"),  # 3 open trades
    ]
    
    with pytest.raises(RiskBreach, match="Max open positions exceeded"):
        enforce_risk_limits(db_session, cfg, trades, None)


def test_risk_guard_counts_distinct_symbols_not_dca_layers(db_session):
    """Regression: pyramid-DCA layers of the SAME symbol are ONE position.

    Each DCA entry is its own Trade row. Counting raw rows (len(open_trades))
    over-counts and false-triggers the cap. The guard must count distinct
    symbols, matching the trading loop's func.count(func.distinct(Trade.symbol)).

    Real incident: 6 live positions held 11 DB rows (AVAX had 4 layers); the
    old len()-based check raised 11 > 10 and killed the live loop on a flat book.
    """
    cfg = RiskConfig(
        max_position_risk_pct=1.0,
        max_portfolio_drawdown_pct=20.0,
        max_daily_loss_pct=5.0,
        max_positions=10,
        max_open_positions=10,
        sl_cooldown_minutes=30,
    )

    # 11 rows across only 6 distinct symbols (AVAX = 4 DCA layers) → 6 <= 10, OK.
    trades = [
        Trade(symbol="AVAXUSDT", direction="SELL", quantity=3.0, entry_price=6.78, status="filled"),
        Trade(symbol="AVAXUSDT", direction="SELL", quantity=3.0, entry_price=6.80, status="filled"),
        Trade(symbol="AVAXUSDT", direction="SELL", quantity=3.0, entry_price=6.77, status="filled"),
        Trade(symbol="AVAXUSDT", direction="SELL", quantity=3.0, entry_price=6.73, status="filled"),
        Trade(symbol="LINKUSDT", direction="BUY", quantity=3.18, entry_price=7.88, status="filled"),
        Trade(symbol="LINKUSDT", direction="BUY", quantity=3.16, entry_price=7.91, status="filled"),
        Trade(symbol="ETHUSDT", direction="BUY", quantity=0.015, entry_price=1674.5, status="filled"),
        Trade(symbol="ETHUSDT", direction="BUY", quantity=0.015, entry_price=1682.9, status="filled"),
        Trade(symbol="BNBUSDT", direction="BUY", quantity=0.04, entry_price=606.5, status="filled"),
        Trade(symbol="BTCUSDT", direction="BUY", quantity=0.001, entry_price=63196.9, status="filled"),
        Trade(symbol="SOLUSDT", direction="BUY", quantity=0.4, entry_price=66.22, status="filled"),
    ]

    # Must NOT raise: 6 distinct symbols within the cap of 10.
    enforce_risk_limits(db_session, cfg, trades, None)


def test_risk_guard_blocks_excessive_drawdown(db_session):
    cfg = RiskConfig(
        max_position_risk_pct=1.0,
        max_portfolio_drawdown_pct=20.0,  # 20% limit
        max_daily_loss_pct=5.0,
        max_open_positions=10,
        sl_cooldown_minutes=30
    )
    
    # Historic peak value is 10000
    snap_peak = PortfolioSnapshot(
        total_value=10000.0,
        cash=10000.0,
        timestamp=datetime.now(timezone.utc) - timedelta(hours=3)
    )
    db_session.add(snap_peak)
    
    # Current value dropped to 7800 (22% drawdown)
    snap_low = PortfolioSnapshot(
        total_value=7800.0,
        cash=7800.0,
        timestamp=datetime.now(timezone.utc)
    )
    db_session.add(snap_low)
    db_session.commit()
    
    with pytest.raises(RiskBreach, match="drawdown exceeded"):
        enforce_risk_limits(db_session, cfg, [], snap_low)

def test_risk_guard_blocks_excessive_daily_loss(db_session):
    cfg = RiskConfig(
        max_position_risk_pct=1.0,
        max_portfolio_drawdown_pct=20.0,
        max_daily_loss_pct=5.0,  # 5% limit
        max_open_positions=10,
        sl_cooldown_minutes=30
    )
    
    # Start of today (UTC) value was 10000
    start_time = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=10)
    snap_today_start = PortfolioSnapshot(
        total_value=10000.0,
        cash=10000.0,
        timestamp=start_time
    )
    db_session.add(snap_today_start)
    
    # Drop to 9400 later today (6% daily loss)
    snap_now = PortfolioSnapshot(
        total_value=9400.0,
        cash=9400.0,
        timestamp=datetime.now(timezone.utc)
    )
    db_session.add(snap_now)
    db_session.commit()
    
    with pytest.raises(RiskBreach, match="daily loss exceeded"):
        enforce_risk_limits(db_session, cfg, [], snap_now)


def test_risk_guard_blocks_excessive_directional_exposure(db_session):
    # Exposure cap of $500; two open trades totalling $600 notional must trip.
    cfg = RiskConfig(max_directional_exposure_usdt=500.0, max_positions=10, max_open_positions=10)
    trades = [
        Trade(symbol="BTCUSDT", direction="BUY", quantity=0.005, entry_price=60000.0, status="open"),  # $300
        Trade(symbol="ETHUSDT", direction="BUY", quantity=0.1, entry_price=3000.0, status="open"),      # $300
    ]
    with pytest.raises(RiskBreach, match="directional exposure exceeded"):
        enforce_risk_limits(db_session, cfg, trades, None)


def test_risk_guard_allows_exposure_within_cap(db_session):
    cfg = RiskConfig(max_directional_exposure_usdt=500.0, max_positions=10, max_open_positions=10)
    trades = [
        Trade(symbol="BTCUSDT", direction="BUY", quantity=0.001, entry_price=60000.0, status="open"),  # $60
    ]
    enforce_risk_limits(db_session, cfg, trades, None)  # no raise


def test_disable_risk_guard_ignored_in_live_mode(db_session, monkeypatch):
    # DISABLE_RISK_GUARD must NOT bypass guards when trading live.
    monkeypatch.setenv("DISABLE_RISK_GUARD", "true")
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("DRY_RUN_ALL", "false")
    cfg = RiskConfig(max_positions=1, max_open_positions=1)
    trades = [
        Trade(symbol="BTCUSDT", direction="BUY", quantity=0.1, entry_price=1.0, status="open"),
        Trade(symbol="ETHUSDT", direction="BUY", quantity=0.1, entry_price=1.0, status="open"),
    ]
    with pytest.raises(RiskBreach, match="Max open positions exceeded"):
        enforce_risk_limits(db_session, cfg, trades, None)


def test_disable_risk_guard_honored_in_paper_mode(db_session, monkeypatch):
    monkeypatch.setenv("DISABLE_RISK_GUARD", "true")
    monkeypatch.setenv("DRY_RUN_ALL", "true")  # -> paper mode
    cfg = RiskConfig(max_positions=1, max_open_positions=1)
    trades = [Trade(symbol="BTCUSDT", direction="BUY", quantity=0.1, entry_price=1.0, status="open")] * 3
    # Disabled in paper mode -> returns without raising even though over cap.
    enforce_risk_limits(db_session, cfg, trades, None)


def test_daily_loss_uses_prior_day_baseline_when_no_snapshot_today(db_session):
    # Cold start of a new day: no snapshot row persisted for today yet, but the
    # caller passes a live latest_snapshot reflecting current equity. The check
    # must fall back to yesterday's close as baseline instead of skipping
    # entirely (the old fail-open behaviour). latest_snapshot is intentionally
    # NOT added to the DB, mirroring a live snapshot not yet committed.
    cfg = RiskConfig(max_daily_loss_pct=5.0, max_portfolio_drawdown_pct=99.0,
                     max_positions=10, max_open_positions=10)
    start_today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    # Yesterday's close persisted at 10000 (the fallback baseline)
    db_session.add(PortfolioSnapshot(total_value=10000.0, cash=10000.0,
                                     timestamp=start_today - timedelta(hours=2)))
    db_session.commit()
    # Live equity now 9400 (-6%), passed in but not persisted -> no today row.
    live = PortfolioSnapshot(total_value=9400.0, cash=9400.0,
                             timestamp=datetime.now(timezone.utc))
    with pytest.raises(RiskBreach, match="daily loss exceeded"):
        enforce_risk_limits(db_session, cfg, [], live)
