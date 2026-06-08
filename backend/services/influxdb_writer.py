"""
InfluxDB Writer Service
Writes trading metrics to InfluxDB buckets for Grafana dashboards.

Buckets:
  trading-raw      – OHLCV tick data (30d retention)
  trading-signals  – AI/strategy signals (90d retention)
  trading-orders   – Trades, fills, PnL (1y retention)
  trading-memory   – Agent state / inter-layer memory (unlimited)
  trading-system   – System health, latency, errors (7d retention)
  news-sentiment   – Processed news/sentiment from n8n (90d retention)
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class InfluxDBWriter:
    """Async InfluxDB v2 writer using line protocol over HTTP."""

    BUCKET_SIGNALS = "trading-signals"
    BUCKET_ORDERS  = "trading-orders"
    BUCKET_RAW     = "trading-raw"
    BUCKET_MEMORY  = "trading-memory"
    BUCKET_SYSTEM  = "trading-system"
    BUCKET_NEWS    = "news-sentiment"

    def __init__(self):
        self.url   = os.getenv("INFLUXDB_URL", "").rstrip("/")
        self.token = os.getenv("INFLUXDB_TOKEN", "")
        self.org   = os.getenv("INFLUXDB_ORG", "-")
        self._enabled = bool(self.token and self.url)
        if not self._enabled:
            logger.warning("InfluxDB token not set – metrics disabled")

    # ─────────────────────────── public API ───────────────────────────── #

    async def write_signal(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        entry_price: float,
        stop_loss: Optional[float],
        take_profit: Optional[float],
        strategy: str = "combined",
        ai_used: bool = False,
        signal_id: Optional[int] = None,
    ) -> None:
        """Write a trading signal to trading-signals bucket."""
        tags = {
            "symbol": symbol,
            "direction": direction,
            "strategy": strategy,
            "ai_used": str(ai_used).lower(),
        }
        fields: dict[str, Any] = {
            "confidence": float(confidence),
            "entry_price": float(entry_price),
        }
        if stop_loss is not None:
            fields["stop_loss"] = float(stop_loss)
        if take_profit is not None:
            fields["take_profit"] = float(take_profit)
        if signal_id is not None:
            fields["signal_id"] = int(signal_id)

        await self._write(self.BUCKET_SIGNALS, "trading_signal", tags, fields)

    async def write_trade(
        self,
        symbol: str,
        direction: str,
        quantity: float,
        entry_price: float,
        status: str,
        strategy: str = "combined",
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        pnl: Optional[float] = None,
        trade_id: Optional[int] = None,
    ) -> None:
        """Write a trade/order event to trading-orders bucket."""
        tags = {
            "symbol": symbol,
            "direction": direction,
            "strategy": strategy,
            "status": status,
        }
        fields: dict[str, Any] = {
            "quantity": float(quantity),
            "entry_price": float(entry_price),
        }
        if stop_loss is not None:
            fields["stop_loss"] = float(stop_loss)
        if take_profit is not None:
            fields["take_profit"] = float(take_profit)
        if pnl is not None:
            fields["pnl"] = float(pnl)
        if trade_id is not None:
            fields["trade_id"] = int(trade_id)

        await self._write(self.BUCKET_ORDERS, "trade", tags, fields)

    async def write_ohlcv(
        self,
        symbol: str,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        timeframe: str = "5m",
        ts: Optional[int] = None,
    ) -> None:
        """Write OHLCV bar to trading-raw bucket."""
        tags = {"symbol": symbol, "timeframe": timeframe}
        fields: dict[str, Any] = {
            "open": float(open_),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": float(volume),
        }
        await self._write(self.BUCKET_RAW, "ohlcv", tags, fields, ts=ts)

    async def write_portfolio_snapshot(
        self,
        cash: float,
        equity: float,
        margin_used: float,
        open_positions: int,
        cycle: int,
    ) -> None:
        """Write portfolio snapshot to trading-orders bucket."""
        tags = {"source": "portfolio"}
        fields: dict[str, Any] = {
            "cash": float(cash),
            "equity": float(equity),
            "margin_used": float(margin_used),
            "open_positions": int(open_positions),
            "cycle": int(cycle),
        }
        await self._write(self.BUCKET_ORDERS, "portfolio_snapshot", tags, fields)

    async def write_system_health(
        self,
        cycle: int,
        symbols_scanned: int,
        signals_generated: int,
        trades_executed: int,
        cycle_duration_ms: float,
        errors: int = 0,
        state: str = "running",
    ) -> None:
        """Write system health metrics to trading-system bucket."""
        tags = {"state": state}
        fields: dict[str, Any] = {
            "cycle": int(cycle),
            "symbols_scanned": int(symbols_scanned),
            "signals_generated": int(signals_generated),
            "trades_executed": int(trades_executed),
            "cycle_duration_ms": float(cycle_duration_ms),
            "errors": int(errors),
        }
        await self._write(self.BUCKET_SYSTEM, "system_health", tags, fields)

    async def write_performance(
        self,
        equity: float,
        realized_pnl: float,
        win_rate: float,
        wins: int,
        losses: int,
        total_trades: int,
        drawdown_pct: float,
        open_positions: int,
        cycle: int = 0,
    ) -> None:
        """Write rolling performance metrics (win rate, drawdown, equity) to
        trading-system bucket so Grafana can chart bot performance over time."""
        fields: dict[str, Any] = {
            "equity": float(equity),
            "realized_pnl": float(realized_pnl),
            "win_rate": float(win_rate),
            "wins": int(wins),
            "losses": int(losses),
            "total_trades": int(total_trades),
            "drawdown_pct": float(drawdown_pct),
            "open_positions": int(open_positions),
            "cycle": int(cycle),
        }
        await self._write(self.BUCKET_SYSTEM, "performance", {}, fields)

    async def write_agent_state(
        self,
        agent: str,
        symbol: str,
        direction: str,
        confidence: float,
        reasoning: str = "",
        session_id: Optional[str] = None,
    ) -> None:
        """Write agent state to trading-memory bucket (inter-layer communication)."""
        tags = {"agent": agent, "symbol": symbol, "direction": direction}
        fields: dict[str, Any] = {
            "confidence": float(confidence),
            "reasoning_len": len(reasoning),
        }
        if session_id:
            fields["session_id"] = f'"{session_id}"'

        await self._write(self.BUCKET_MEMORY, "agent_state", tags, fields)

    async def write_news_sentiment(
        self,
        symbol: str,
        sentiment_score: float,
        impact_score: float,
        source: str = "newsapi",
        time_horizon: str = "short",
        topics: str = "",
        confidence: float = 0.0,
        direction: str = "NEUTRAL",
    ) -> None:
        """Write processed news sentiment to news-sentiment bucket.

        Includes `confidence` and `direction` tag so that the InfluxDB
        sentiment reader can read them directly instead of re-deriving.
        """
        # Direction tag must be one of the values the reader understands
        _valid = {"BULLISH", "BEARISH", "NEUTRAL", "BUY", "SELL"}
        direction_tag = direction.upper() if direction.upper() in _valid else "NEUTRAL"

        tags = {"symbol": symbol, "source": source, "time_horizon": time_horizon,
                "direction": direction_tag}
        fields: dict[str, Any] = {
            "sentiment_score": float(sentiment_score),
            "impact_score": float(impact_score),
            "confidence": float(confidence),
            "topics_len": len(topics),
        }
        await self._write(self.BUCKET_NEWS, "news_sentiment", tags, fields)

    # ─────────────────────────── internals ────────────────────────────── #

    @staticmethod
    def _escape_tag(v: str) -> str:
        return str(v).replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")

    @staticmethod
    def _field_value(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return f"{v}i"
        if isinstance(v, float):
            return f"{v:.6f}"
        # Already-formatted string field (e.g. quoted)
        if isinstance(v, str) and v.startswith('"') and v.endswith('"'):
            return v
        return f'"{str(v)}"'

    def _build_line(self, measurement: str, tags: dict, fields: dict, ts: Optional[int] = None) -> str:
        tag_str = ",".join(
            f"{self._escape_tag(k)}={self._escape_tag(v)}"
            for k, v in sorted(tags.items())
        )
        field_str = ",".join(
            f"{k}={self._field_value(v)}"
            for k, v in fields.items()
        )
        line = f"{measurement},{tag_str} {field_str}"
        if ts is not None:
            line += f" {ts}"
        return line

    async def _write(self, bucket: str, measurement: str, tags: dict, fields: dict, ts: Optional[int] = None) -> None:
        if not self._enabled:
            return
        if not fields:
            return

        line = self._build_line(measurement, tags, fields, ts)
        url = f"{self.url}/api/v2/write"
        params = {"org": self.org, "bucket": bucket, "precision": "ns" if ts else "s"}
        headers = {
            "Authorization": f"Token {self.token}",
            "Content-Type": "text/plain; charset=utf-8",
        }

        max_retries = 3
        backoff = 0.5  # start with 500ms
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(url, params=params, headers=headers, content=line)
                    if resp.status_code in (200, 204):
                        return
                    else:
                        logger.warning(
                            f"InfluxDB write error [{bucket}] (attempt {attempt + 1}/{max_retries}): "
                            f"{resp.status_code} {resp.text[:200]}"
                        )
            except Exception as exc:
                logger.warning(
                    f"InfluxDB write failed [{bucket}] (attempt {attempt + 1}/{max_retries}): {exc}"
                )
            
            if attempt < max_retries - 1:
                await asyncio.sleep(backoff)
                backoff *= 2  # 0.5s -> 1s -> 2s

    async def write_binance_wallet(self, balance: float, available: float,
                                    equity: float, unrealized_pnl: float,
                                    margin_used: float) -> None:
        """Write Binance Futures wallet snapshot to trading-system bucket."""
        await self._write(
            bucket='trading-system',
            measurement='binance_wallet',
            tags={'broker': 'binance_futures', 'account': 'live'},
            fields={
                'balance': float(balance),
                'available': float(available),
                'equity': float(equity),
                'unrealized_pnl': float(unrealized_pnl),
                'margin_used': float(margin_used),
            }
        )

    async def write_binance_position(self, symbol: str, side: str,
                                      quantity: float, entry_price: float,
                                      unrealized_pnl: float, leverage: int,
                                      mark_price: float = 0.0,
                                      liquidation_price: float = 0.0) -> None:
        """Write a single Binance Futures open position to trading-system bucket."""
        await self._write(
            bucket='trading-system',
            measurement='binance_position',
            tags={'symbol': symbol, 'side': side, 'broker': 'binance_futures'},
            fields={
                'quantity': float(quantity),
                'entry_price': float(entry_price),
                'unrealized_pnl': float(unrealized_pnl),
                'leverage': float(leverage),
                'mark_price': float(mark_price),
                'liquidation_price': float(liquidation_price),
                'notional_value': float(quantity * (mark_price or entry_price)),
            }
        )


# Singleton instance
influx = InfluxDBWriter()
