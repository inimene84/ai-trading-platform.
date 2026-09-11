from types import SimpleNamespace

from backend.services.ledger import (
    binance_position_key,
    fill_mode_from_order,
    has_live_venue_id,
    is_binance_paper_fill,
)


def test_paper_prefix_is_paper_fill():
    t = SimpleNamespace(
        broker="binance_futures",
        mode="live",
        binance_order_id="paper_000012",
        broker_position_id=None,
        broker_order_id=None,
    )
    assert is_binance_paper_fill(t) is True
    assert has_live_venue_id(t) is False


def test_live_binance_id_is_not_paper():
    t = SimpleNamespace(
        broker="binance_futures",
        mode="live",
        binance_order_id="889911",
        broker_position_id="OPUSDT:SHORT",
        broker_order_id=None,
    )
    assert is_binance_paper_fill(t) is False
    assert has_live_venue_id(t) is True


def test_default_paper_mode_without_venue_id_is_ghost():
    t = SimpleNamespace(
        broker="binance_futures",
        mode="paper",
        binance_order_id=None,
        broker_position_id=None,
        broker_order_id=None,
    )
    assert is_binance_paper_fill(t) is True
    assert has_live_venue_id(t) is False


def test_ctrader_not_classified_as_binance_paper():
    t = SimpleNamespace(
        broker="ctrader",
        mode="paper",
        binance_order_id=None,
        broker_position_id="41416076",
        broker_order_id=None,
    )
    assert is_binance_paper_fill(t) is False


def test_fill_mode_and_position_key():
    assert fill_mode_from_order("live", "paper") == "live"
    assert fill_mode_from_order(None, "paper") == "paper"
    assert binance_position_key("opusdt", "SELL") == "OPUSDT:SHORT"
