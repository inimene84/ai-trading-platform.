from typing import Protocol, Optional

class IBroker(Protocol):
    """
    Standard interface for all live broker integrations (Binance, cTrader, etc.)
    """

    def get_balance(self) -> dict:
        """
        Fetch the current wallet balance and margin usage.
        Returns a dict with at least:
        - 'balance': float
        - 'available': float
        - 'equity': float
        - 'margin_used': float
        - 'unrealized_pnl': float
        - 'broker': str
        """
        ...

    def get_positions(self) -> list:
        """
        Return all non-zero open positions.
        Returns a list of dicts with at least:
        - 'symbol': str
        - 'side': 'BUY' | 'SELL'
        - 'quantity': float
        - 'entry_price': float
        - 'unrealized_pnl': float
        """
        ...

    def place_order(
        self,
        symbol: str,
        direction: str,
        action: str = 'open',
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        **kwargs
    ) -> dict:
        """
        Place a new market order or close an existing position.
        Returns a dict with at least:
        - 'status': 'sent' | 'simulated' | 'already_flat' | 'error' | 'skipped'
        - 'broker': str
        - 'order_id': str
        """
        ...

    def cancel_order(self, order_id: str) -> dict:
        """
        Cancel an open order.
        Returns a dict with at least:
        - 'success': bool
        - 'message': str
        """
        ...
