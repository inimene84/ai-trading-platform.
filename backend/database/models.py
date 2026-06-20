from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, JSON, ForeignKey, Float
from sqlalchemy.sql import func
from .connection import Base


class HedgeFundFlow(Base):
    """Table to store React Flow configurations (nodes, edges, viewport)"""
    __tablename__ = "hedge_fund_flows"
    
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Flow metadata
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    
    # React Flow state
    nodes = Column(JSON, nullable=False)  # Store React Flow nodes as JSON
    edges = Column(JSON, nullable=False)  # Store React Flow edges as JSON
    viewport = Column(JSON, nullable=True)  # Store viewport state (zoom, x, y)
    data = Column(JSON, nullable=True)  # Store node internal states (tickers, models, etc.)
    
    # Additional metadata
    is_template = Column(Boolean, default=False)  # Mark as template for reuse
    tags = Column(JSON, nullable=True)  # Store tags for categorization


class HedgeFundFlowRun(Base):
    """Table to track individual execution runs of a hedge fund flow"""
    __tablename__ = "hedge_fund_flow_runs"
    
    id = Column(Integer, primary_key=True, index=True)
    flow_id = Column(Integer, ForeignKey("hedge_fund_flows.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Run execution tracking
    status = Column(String(50), nullable=False, default="IDLE")  # IDLE, IN_PROGRESS, COMPLETE, ERROR
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Run configuration
    trading_mode = Column(String(50), nullable=False, default="one-time")  # one-time, continuous, advisory
    schedule = Column(String(50), nullable=True)  # hourly, daily, weekly (for continuous mode)
    duration = Column(String(50), nullable=True)  # 1day, 1week, 1month (for continuous mode)
    
    # Run data
    request_data = Column(JSON, nullable=True)  # Store the request parameters (tickers, agents, models, etc.)
    initial_portfolio = Column(JSON, nullable=True)  # Store initial portfolio state
    final_portfolio = Column(JSON, nullable=True)  # Store final portfolio state
    results = Column(JSON, nullable=True)  # Store the output/results from the run
    error_message = Column(Text, nullable=True)  # Store error details if run failed
    
    # Metadata
    run_number = Column(Integer, nullable=False, default=1)  # Sequential run number for this flow


class HedgeFundFlowRunCycle(Base):
    """Individual analysis cycles within a trading session"""
    __tablename__ = "hedge_fund_flow_run_cycles"
    
    id = Column(Integer, primary_key=True, index=True)
    flow_run_id = Column(Integer, ForeignKey("hedge_fund_flow_runs.id"), nullable=False, index=True)
    cycle_number = Column(Integer, nullable=False)  # 1, 2, 3, etc. within the run
    
    # Timing
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Analysis results
    analyst_signals = Column(JSON, nullable=True)  # All agent decisions/signals
    trading_decisions = Column(JSON, nullable=True)  # Portfolio manager decisions
    executed_trades = Column(JSON, nullable=True)  # Actual trades executed (paper trading)
    
    # Portfolio state after this cycle
    portfolio_snapshot = Column(JSON, nullable=True)  # Cash, positions, performance metrics
    
    # Performance metrics for this cycle
    performance_metrics = Column(JSON, nullable=True)  # Returns, sharpe ratio, etc.
    
    # Execution tracking
    status = Column(String(50), nullable=False, default="IN_PROGRESS")  # IN_PROGRESS, COMPLETED, ERROR
    error_message = Column(Text, nullable=True)  # Store error details if cycle failed
    
    # Cost tracking
    llm_calls_count = Column(Integer, nullable=True, default=0)  # Number of LLM calls made
    api_calls_count = Column(Integer, nullable=True, default=0)  # Number of financial API calls made
    estimated_cost = Column(String(20), nullable=True)  # Estimated cost in USD
    
    # Metadata
    trigger_reason = Column(String(100), nullable=True)  # scheduled, manual, market_event, etc.
    market_conditions = Column(JSON, nullable=True)  # Market data snapshot at cycle start


class ApiKey(Base):
    """Table to store API keys for various services"""
    __tablename__ = "api_keys"
    
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # API key details
    provider = Column(String(100), nullable=False, unique=True, index=True)  # e.g., "ANTHROPIC_API_KEY"
    key_value = Column(Text, nullable=False)  # The actual API key (encrypted in production)
    is_active = Column(Boolean, default=True)  # Enable/disable without deletion
    
    # Optional metadata
    description = Column(Text, nullable=True)  # Human-readable description
    last_used = Column(DateTime(timezone=True), nullable=True)  # Track usage



class TradingSignal(Base):
    """Generated trading signals from strategy analysis"""
    __tablename__ = "trading_signals"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    symbol = Column(String(20), nullable=False, index=True)
    strategy = Column(String(50), nullable=False)
    direction = Column(String(10), nullable=False)  # BUY, SELL, HOLD
    confidence = Column(Float, nullable=False, default=0.0)
    entry_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, default="pending")  # pending, executed, expired, rejected
    reasoning = Column(Text, nullable=True)
    ai_analysis = Column(Text, nullable=True)  # JSON string of AI analysis steps


class Trade(Base):
    """Executed trades (paper or live)"""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    symbol = Column(String(20), nullable=False, index=True)
    direction = Column(String(10), nullable=False)  # BUY, SELL
    quantity = Column(Float, nullable=False, default=0.0)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, default="open")  # open, closed
    pnl = Column(Float, nullable=True, default=0.0)
    strategy = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    signal_id = Column(Integer, nullable=True)
    binance_order_id = Column(String(50), nullable=True, index=True)
    exchange = Column(String(20), nullable=True, default='binance_futures')
    filled_price = Column(Float, nullable=True)


class PortfolioSnapshot(Base):
    """Portfolio state snapshots for equity curve tracking"""
    __tablename__ = "portfolio_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    total_value = Column(Float, nullable=False, default=10000.0)
    cash = Column(Float, nullable=False, default=10000.0)
    positions_value = Column(Float, nullable=False, default=0.0)
    total_pnl = Column(Float, nullable=False, default=0.0)
    open_positions = Column(Integer, nullable=False, default=0)
    cycle_number = Column(Integer, nullable=True)


# ═══ Paper Trading Persistence Models ═══ (Fincept Port A)

class PaperPortfolio(Base):
    """Paper trading portfolio accounts"""
    __tablename__ = "paper_portfolios"

    id = Column(Integer, primary_key=True, index=True)
    portfolio_id = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    broker = Column(String(50), nullable=False)
    currency = Column(String(10), default="USD")
    leverage = Column(Float, default=1.0)
    margin_mode = Column(String(20), default="cross")
    fee_rate = Column(Float, default=0.001)
    initial_balance = Column(Float, nullable=False)
    cash = Column(Float, nullable=False)
    margin_used = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class PaperOrder(Base):
    """Paper trading orders"""
    __tablename__ = "paper_orders"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(String(50), unique=True, nullable=False, index=True)
    portfolio_id = Column(String(20), nullable=False, index=True)
    symbol = Column(String(20), nullable=False)
    side = Column(String(10), nullable=False)
    order_type = Column(String(20), nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float, default=0.0)
    stop_price = Column(Float, default=0.0)
    filled_qty = Column(Float, default=0.0)
    avg_price = Column(Float, default=0.0)
    status = Column(String(20), default="pending")
    reduce_only = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    filled_at = Column(DateTime(timezone=True), nullable=True)


class PaperTrade(Base):
    """Paper trading fills / executed trades"""
    __tablename__ = "paper_trades"

    id = Column(Integer, primary_key=True, index=True)
    trade_id = Column(String(50), unique=True, nullable=False, index=True)
    order_id = Column(String(50), nullable=False, index=True)
    portfolio_id = Column(String(20), nullable=False, index=True)
    symbol = Column(String(20), nullable=False)
    side = Column(String(10), nullable=False)
    price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    fee = Column(Float, default=0.0)
    pnl = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# ═══ Track C+ : Learned Strategy Skills (skill miner) ═══

class StrategySkill(Base):
    """A learned, named trading 'skill' mined from the agent's own trade history.

    Each row is a recurring market-setup archetype (a cluster of similar trades)
    together with its realised edge. The opinion layer matches the live setup to
    the best-fitting skill and votes with the skill's historical bias/confidence.
    """
    __tablename__ = "strategy_skills"

    id = Column(Integer, primary_key=True, index=True)
    skill_key = Column(String(64), unique=True, nullable=False, index=True)  # stable hash of the centroid
    name = Column(String(120), nullable=False)        # human-readable, e.g. "Trending + bullish momentum (BTC)"
    description = Column(Text, nullable=True)

    # Centroid of the cluster in feature space (the "what the market looks like").
    centroid = Column(JSON, nullable=False)            # list[float], normalised feature vector
    feature_summary = Column(JSON, nullable=True)      # human-readable feature dict at the centroid

    # Realised edge
    direction = Column(String(10), nullable=False, default="neutral")  # bullish | bearish | neutral
    sample_count = Column(Integer, nullable=False, default=0)
    win_rate = Column(Float, nullable=False, default=0.0)
    avg_pnl = Column(Float, nullable=False, default=0.0)
    total_pnl = Column(Float, nullable=False, default=0.0)
    sharpe = Column(Float, nullable=True)              # avg_pnl / std(pnl), consistency proxy
    edge_score = Column(Float, nullable=False, default=0.0)   # composite 0..1 used for ranking/confidence

    symbols = Column(JSON, nullable=True)              # symbols that contributed
    active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_mined_at = Column(DateTime(timezone=True), nullable=True)
