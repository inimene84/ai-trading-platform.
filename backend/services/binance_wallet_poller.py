"""
Standalone Binance Futures wallet poller.
Runs as a background task, writes wallet + positions to InfluxDB every 30 seconds.
Independent of the trading loop - always active when backend starts.
"""
import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 30  # seconds
_task = None


async def start_wallet_poller():
    """Start the background wallet polling loop (idempotent)."""
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_poll_loop())
    logger.info("Binance wallet poller started (interval=30s)")


async def _poll_loop():
    """Main polling loop - runs until process exits."""
    # Import here to avoid circular imports at module load time
    from backend.services.influxdb_writer import influx

    # Create a dedicated Binance service instance
    try:
        from backend.services.binance_futures_service import BinanceFuturesService
        svc = BinanceFuturesService()
        logger.info("Wallet poller: BinanceFuturesService initialized")
    except Exception as e:
        logger.error(f"Wallet poller: failed to init BinanceFuturesService: {e}")
        return

    # Poll loop
    while True:
        try:
            # Only run when Binance Futures is the active broker
            broker_name = os.getenv("ACTIVE_BROKER", "ctrader")
            if broker_name == "binance_futures":
                await _write_wallet_and_positions(svc, influx)
        except Exception as e:
            logger.warning(f"Wallet poller cycle error: {e}")
        await asyncio.sleep(_POLL_INTERVAL)


async def _write_wallet_and_positions(svc, influx):
    """Fetch wallet + positions from Binance (or paper portfolio) and write to InfluxDB."""
    paper_mode = os.getenv("PAPER_TRADING", "false").lower() == "true"
    
    if paper_mode:
        # Paper trading mode: read from paper portfolio
        try:
            from backend.services.unified_trading import trading_router
            
            # Get the default session's paper portfolio
            portfolio_id = trading_router._default_pf_id()
            if not portfolio_id:
                logger.info("Wallet poller: No paper portfolio found")
                return
            
            pf = trading_router.get_paper_portfolio()
            if not pf:
                logger.info("Wallet poller: Paper portfolio not found")
                return
            
            # Extract balance info from paper portfolio
            cash = float(pf.get('cash', 0.0))
            equity = float(pf.get('equity', cash))
            margin_used = float(pf.get('margin_used', 0.0))
            unrealized_pnl = equity - cash - margin_used
            
            await influx.write_binance_wallet(
                balance=cash,
                available=cash,
                equity=equity,
                unrealized_pnl=unrealized_pnl,
                margin_used=margin_used,
            )
            logger.info(f"[Paper] wallet → InfluxDB: equity={equity:.2f} USDT, cash={cash:.2f}, pnl={unrealized_pnl:.4f}")
            
            # Write paper positions
            positions = trading_router.get_paper_positions()
            for pos in positions:
                # BrokerPosition is a dataclass with attributes, not a dict
                symbol = pos.symbol if hasattr(pos, 'symbol') else 'UNKNOWN'
                side = pos.side if hasattr(pos, 'side') else 'long'
                qty = pos.quantity if hasattr(pos, 'quantity') else 0.0
                entry = pos.avg_price if hasattr(pos, 'avg_price') else 0.0
                upnl = pos.unrealized_pnl if hasattr(pos, 'unrealized_pnl') else 0.0
                # current_price can be used as mark_price
                mark = pos.current_price if hasattr(pos, 'current_price') else 0.0
                
                await influx.write_binance_position(
                    symbol=symbol,
                    side=side,
                    quantity=qty,
                    entry_price=entry,
                    unrealized_pnl=upnl,
                    leverage=1,  # Default leverage for paper positions
                    mark_price=mark if mark > 0 else entry,
                )
            if positions:
                logger.info(f"[Paper] {len(positions)} positions written to InfluxDB")
        except Exception as e:
            logger.warning(f"Wallet poller: paper portfolio error: {e}")
    else:
        # Live trading mode: read from Binance API
        bal = svc.get_balance()
        if not bal:
            return

        raw_balance = float(bal.get('balance', 0.0))
        equity = float(bal.get('equity', 0.0))
        available = float(bal.get('available', 0.0))
        unrealized_pnl = float(bal.get('unrealized_pnl', 0.0))
        margin_used = float(bal.get('margin_used', 0.0))

        # Binance futures: 'balance' from futures_account_balance() may return 0
        # while equity from futures_account totalWalletBalance is correct
        # Use equity as primary balance figure when raw_balance is 0
        display_balance = equity if raw_balance == 0.0 and equity > 0 else raw_balance
        display_available = available if available > 0 else equity - margin_used

        await influx.write_binance_wallet(
            balance=display_balance,
            available=display_available,
            equity=equity,
            unrealized_pnl=unrealized_pnl,
            margin_used=margin_used,
        )
        logger.info(f"[Binance] wallet → InfluxDB: equity={equity:.2f} USDT, available={display_available:.2f}, pnl={unrealized_pnl:.4f}")

        # Write open positions
        try:
            positions = svc.get_positions()
            for pos in positions:
                await influx.write_binance_position(
                    symbol=pos.get('symbol', 'UNKNOWN'),
                    side=pos.get('side', 'LONG'),
                    quantity=float(pos.get('quantity', 0.0)),
                    entry_price=float(pos.get('entry_price', 0.0)),
                    unrealized_pnl=float(pos.get('unrealized_pnl', 0.0)),
                    leverage=int(pos.get('leverage', 10)),
                    mark_price=float(pos.get('mark_price', 0.0)),
                    liquidation_price=float(pos.get('liquidation_price', 0.0)),
                )
            if positions:
                logger.info(f"[Binance] {len(positions)} positions written to InfluxDB")
        except Exception as e:
            logger.warning(f"Wallet poller: positions write error: {e}")
