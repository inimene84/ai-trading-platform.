"""LOT_SIZE step rounding — prevents Binance -1111 on symbols like UNIUSDT."""
from backend.services.binance_futures_service import BinanceFuturesService


def _broker_with_lot(sym: str, step: float, min_qty: float, prec: int = 0):
    svc = BinanceFuturesService.__new__(BinanceFuturesService)
    svc._lot_step = {sym: step}
    svc._lot_min = {sym: min_qty}
    svc._qty_precision = {sym: prec}
    return svc


def test_round_qty_uni_whole_units():
    """UNIUSDT stepSize=1 — 10.088 must become 10, not 10.1."""
    broker = _broker_with_lot("UNIUSDT", step=1.0, min_qty=1.0, prec=0)
    assert broker._round_qty("UNIUSDT", 10.088781) == 10.0


def test_round_qty_bumps_to_min():
    broker = _broker_with_lot("UNIUSDT", step=1.0, min_qty=1.0, prec=0)
    assert broker._round_qty("UNIUSDT", 0.5) == 1.0


def test_round_qty_fractional_step():
    broker = _broker_with_lot("BTCUSDT", step=0.001, min_qty=0.001, prec=3)
    assert broker._round_qty("BTCUSDT", 0.000314) == 0.001
