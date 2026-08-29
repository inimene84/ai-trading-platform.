"""
Unit tests for BrokerCircuitBreaker.
Verifies threshold tripping, cooldown periods, and broker isolation.
"""
import time
import pytest
from backend.services.broker_circuit_breaker import BrokerCircuitBreaker


def test_circuit_breaker_initial_state():
    cb = BrokerCircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
    avail, reason = cb.is_available("ctrader")
    assert avail is True
    assert reason is None


def test_circuit_breaker_trips_on_threshold():
    cb = BrokerCircuitBreaker(failure_threshold=2, cooldown_seconds=60.0)

    cb.record_error("ctrader", "Socket timeout")
    avail, reason = cb.is_available("ctrader")
    assert avail is True  # 1 of 2 errors, not yet tripped

    cb.record_error("ctrader", "Auth failed")
    avail, reason = cb.is_available("ctrader")
    assert avail is False  # 2 of 2 errors, now tripped
    assert "Circuit breaker active" in reason

    # Binance remains available (isolated)
    binance_avail, binance_reason = cb.is_available("binance_futures")
    assert binance_avail is True
    assert binance_reason is None


def test_circuit_breaker_reset_on_success():
    cb = BrokerCircuitBreaker(failure_threshold=3, cooldown_seconds=60.0)
    cb.record_error("ctrader", "Error 1")
    cb.record_error("ctrader", "Error 2")
    cb.record_success("ctrader")

    # Error count should be reset to 0
    cb.record_error("ctrader", "Error 1 again")
    avail, _ = cb.is_available("ctrader")
    assert avail is True


def test_circuit_breaker_cooldown_expiry():
    cb = BrokerCircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
    cb.record_error("ctrader", "Fast trip")
    assert cb.is_available("ctrader")[0] is False

    time.sleep(0.06)
    assert cb.is_available("ctrader")[0] is True
