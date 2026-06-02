import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone, timedelta

from backend.database.models import Base, Trade, PortfolioSnapshot
from backend.services.risk_config import RiskConfig
from backend.services.risk_guard import enforce_risk_limits, RiskBreach

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
