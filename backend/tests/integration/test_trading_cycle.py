import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from backend.services.trading_loop import TradingLoopService
from backend.services.unified_trading import UnifiedTrading
from backend.tests.mocks.mock_broker import MockBroker
from backend.database.models import Base, PortfolioSnapshot
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone

@pytest.fixture
def clean_db():
    # Setup isolated memory database for integration test cycle
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        # Create a mock starting portfolio snapshot to satisfy risk guard
        snap = PortfolioSnapshot(total_value=10000.0, cash=10000.0)
        session.add(snap)
        session.commit()
        yield session
    finally:
        session.close()

@pytest.mark.anyio
async def test_single_cycle_paper_mode(monkeypatch, clean_db):
    # Force paper mode and BTCUSDT only
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("TRADING_SYMBOLS", "BTCUSDT")

    mock_broker = MockBroker()
    ut = UnifiedTrading()
    ut.register_broker("binance_futures", mock_broker)

    loop = TradingLoopService()
    loop._unified_trading = ut
    loop._interval_minutes = 5
    loop._symbols = ["BTCUSDT"]

    # Mock the DB session factory used in loop cycle (patched globally to intercept local function imports)
    with patch("backend.database.connection.SessionLocal", return_value=clean_db), \
         patch("backend.services.trading_loop.SessionLocal", return_value=clean_db):
        # Mock InfluxDB, Sentiment, and Binance Account endpoints to avoid network hits
        with patch("backend.services.trading_loop.binance_futures_broker._get_client") as mock_binance_client, \
             patch("backend.services.trading_loop.influx") as mock_influx, \
             patch("backend.services.trading_loop.binance_market_data") as mock_market_data, \
             patch.object(loop, "_fetch_bars", new_callable=AsyncMock, return_value=[{"close": 50000.0, "high": 51000.0, "low": 49000.0, "open": 49500.0, "volume": 100}]), \
             patch("backend.services.trading_loop.get_active_broker", return_value=mock_broker), \
             patch("backend.services.trading_loop.get_position_manager") as mock_pm:
            
            # Setup mock futures account response for kill switch
            mock_client_instance = MagicMock()
            mock_client_instance.futures_account.return_value = {"totalMarginBalance": "10000.0"}
            mock_binance_client.return_value = mock_client_instance

            # Mock InfluxDB async calls
            mock_influx.write_system_health = AsyncMock()
            mock_influx.write_portfolio_snapshot = AsyncMock()
            mock_influx.write_signal = AsyncMock()
            mock_influx.write_trade = AsyncMock()

            # Mock position manager to hold no positions and return safe emergency drawdown
            mock_pm_instance = MagicMock()
            mock_pm_instance.emergency_drawdown_pct = -15.0
            mock_pm.return_value = mock_pm_instance

            # Execute a single trading cycle
            await loop._run_cycle()

            # Assert cycle successfully completed and incremented the counter
            assert loop._cycle_count == 1
            assert loop._state == "running"
            assert loop._error is None
