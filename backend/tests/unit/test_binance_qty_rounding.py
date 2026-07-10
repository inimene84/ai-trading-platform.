"""LOT_SIZE step rounding — prevents Binance -1111 on symbols like UNIUSDT."""
import pytest

from backend.services.binance_futures_service import MIN_NOTIONAL, BinanceFuturesService


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


def test_round_qty_round_up_clears_next_step():
    """round_up=True must never floor back below the requested minimum."""
    broker = _broker_with_lot("ETHUSDT", step=0.001, min_qty=0.001, prec=3)
    # 20 / 1789.27 = 0.0111769... -> floor lands on 0.011 (the -4164 bug),
    # round_up must land on 0.012 instead.
    qty = broker._round_qty("ETHUSDT", 20.0 / 1789.27, round_up=True)
    assert qty == 0.012
    assert qty * 1789.27 >= 20.0


@pytest.mark.parametrize(
    "sym,step,min_qty,prec,price",
    [
        ("ETHUSDT", 0.001, 0.001, 3, 1789.27),   # reproduces the live -4164 (qty 0.011 -> $19.68)
        ("LINKUSDT", 0.001, 0.001, 3, 7.931),    # reproduces the live -4164 (qty 2.52 -> $19.99)
    ],
)
def test_min_quantity_always_clears_min_notional(monkeypatch, sym, step, min_qty, prec, price):
    """_min_quantity's output must satisfy Binance's MIN_NOTIONAL filter.

    Regression test for a live bug: the raw min-notional quantity (min_notional
    / price) was floored to the LOT_SIZE step, which could round the notional
    back under the threshold and trigger `-4164 MIN_NOTIONAL` on every order.
    """
    monkeypatch.setenv("TRADE_USDT_AMOUNT", "25.0")
    broker = _broker_with_lot(sym, step=step, min_qty=min_qty, prec=prec)
    qty = broker._min_quantity(sym, price)
    min_notional = MIN_NOTIONAL.get(sym, 20.0)
    assert qty * price >= min_notional, (
        f"{sym}: qty={qty} @ ${price} = ${qty * price:.4f} notional, "
        f"below Binance's ${min_notional} minimum"
    )
