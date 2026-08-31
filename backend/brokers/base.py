"""
BrokerService interface definition and common broker data structures.
Compatible with BinanceFuturesService, CTraderService, and UnifiedTrading.
"""
from typing import Protocol, Optional, Any, Dict, List, runtime_checkable


@runtime_checkable
class BrokerService(Protocol):
    """Standard protocol for all live & paper broker integrations."""

    @property
    def is_connected(self) -> bool:
        """Return True if broker connection is authenticated and ready."""
        ...

    @property
    def dry_run(self) -> bool:
        """Return True if orders are simulated in paper mode."""
        ...

    def connect(self) -> bool:
        """Establish connection and authenticate with broker backend."""
        ...

    def disconnect(self) -> None:
        """Gracefully terminate broker connection."""
        ...

    def status(self) -> Dict[str, Any]:
        """Return status dictionary containing connection, balance, and broker metadata."""
        ...

    def get_balance(self) -> Dict[str, Any]:
        """
        Fetch current account balance, equity, and margin.
        Returns:
            {
                'balance': float,
                'available': float,
                'equity': float,
                'margin_used': float,
                'unrealized_pnl': float,
                'broker': str,
            }
        """
        ...

    def get_positions(self) -> List[Dict[str, Any]]:
        """
        Return all open positions.
        Returns list of dicts:
            {
                'symbol': str,
                'side': 'BUY' | 'SELL' | 'LONG' | 'SHORT',
                'quantity': float,
                'entry_price': float,
                'unrealized_pnl': float,
                'position_id': Optional[str | int],
                'broker': str,
            }
        """
        ...

    def place_order(
        self,
        symbol: str = "",
        direction: str = "",
        action: str = "open",
        quantity: Optional[float] = None,
        volume: Optional[float] = None,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Place a new order or modify/close position.
        Returns:
            {
                'status': 'sent' | 'simulated' | 'already_flat' | 'error' | 'skipped',
                'broker': str,
                'order_id': Optional[str],
                'symbol': str,
                'direction': str,
                'quantity': float,
                ...
            }
        """
        ...

    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        Cancel an open order.
        Returns:
            {'success': bool, 'message': str, 'order_id': str}
        """
        ...

    def close_position(self, position_id: str | int, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        Close an existing open position by position ID or symbol.
        """
        ...


# Backward compatibility alias
IBroker = BrokerService
