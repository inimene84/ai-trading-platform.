from typing import List, Dict, Any
from backend.services.unified_trading import UnifiedOrder

class MockBroker:
    """Mock broker adapter satisfying execution requirements in unit/integration tests."""
    
    def __init__(self):
        self.orders: List[UnifiedOrder] = []
        self.positions: Dict[str, Dict[str, Any]] = {}
        self._balance = 10000.0

    def get_balance(self) -> Dict[str, float]:
        return {"balance": self._balance}

    def get_positions(self) -> List[Dict[str, Any]]:
        return [
            {"symbol": s, "quantity": pos["qty"], "mark_price": pos["mark_price"]}
            for s, pos in self.positions.items()
        ]

    def place_order(self, symbol: str, direction: str, quantity: float, price: float = 0,
                    stop_loss: float = 0, take_profit: float = 0, reduce_only: bool = False) -> Dict[str, Any]:
        order_id = f"mock_order_{len(self.orders) + 1}"
        
        # Naive position tracking
        self.positions.setdefault(symbol, {"qty": 0.0, "mark_price": price if price > 0 else 50000.0})
        qty_change = quantity if direction == "BUY" else -quantity
        if reduce_only:
            # simple reduce only logic
            current_qty = self.positions[symbol]["qty"]
            if current_qty > 0 and direction == "SELL":
                qty_change = -min(quantity, current_qty)
            elif current_qty < 0 and direction == "BUY":
                qty_change = min(quantity, abs(current_qty))
                
        self.positions[symbol]["qty"] += qty_change

        order_data = {
            "symbol": symbol,
            "side": direction,
            "quantity": quantity,
            "price": price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "reduce_only": reduce_only
        }
        self.orders.append(order_data)
        
        return {
            "status": "filled",
            "order_id": order_id,
            "message": "Simulated live order filled",
            "filled_price": price if price > 0 else 50000.0,
            "quantity": quantity
        }
