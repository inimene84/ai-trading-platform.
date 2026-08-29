"""
Per-Broker Circuit Breaker.

Ensures that failure or connection loss in one broker (e.g. cTrader Open API)
suspends routing to only that broker for a cooldown period without impacting
the execution loop of other brokers (e.g. Binance Futures).
"""
import logging
import time
from typing import Dict, Tuple, Optional

logger = logging.getLogger(__name__)


class BrokerCircuitBreaker:
    """Tracks error counts and cooldown states per broker."""

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 300.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._consecutive_errors: Dict[str, int] = {}
        self._tripped_until: Dict[str, float] = {}
        self._last_error: Dict[str, str] = {}

    def record_success(self, broker: str) -> None:
        """Reset consecutive error count on successful execution."""
        broker_key = broker.lower().split(":")[0]
        if self._consecutive_errors.get(broker_key, 0) > 0:
            logger.info(f"[CIRCUIT BREAKER] Broker '{broker_key}' recovered. Error count reset.")
        self._consecutive_errors[broker_key] = 0
        self._tripped_until.pop(broker_key, None)

    def record_error(self, broker: str, error: str) -> None:
        """Increment error count and trip circuit breaker if threshold is exceeded."""
        broker_key = broker.lower().split(":")[0]
        count = self._consecutive_errors.get(broker_key, 0) + 1
        self._consecutive_errors[broker_key] = count
        self._last_error[broker_key] = str(error)

        logger.warning(
            f"[CIRCUIT BREAKER] Broker '{broker_key}' error #{count}/{self.failure_threshold}: {error}"
        )

        if count >= self.failure_threshold:
            trip_time = time.time() + self.cooldown_seconds
            self._tripped_until[broker_key] = trip_time
            logger.error(
                f"[CIRCUIT BREAKER] Broker '{broker_key}' TRIPPED. Execution suspended for {self.cooldown_seconds}s. Reason: {error}"
            )

    def is_available(self, broker: str) -> Tuple[bool, Optional[str]]:
        """
        Check if broker is currently available.
        Returns: (is_available, reason_if_tripped)
        """
        broker_key = broker.lower().split(":")[0]
        trip_until = self._tripped_until.get(broker_key)
        if not trip_until:
            return True, None

        now = time.time()
        if now < trip_until:
            remaining = int(trip_until - now)
            reason = f"Circuit breaker active ({remaining}s remaining). Last error: {self._last_error.get(broker_key, 'Unknown')}"
            return False, reason

        # Cooldown expired — allow half-open trial
        logger.info(f"[CIRCUIT BREAKER] Broker '{broker_key}' cooldown expired. Resuming half-open state.")
        self._tripped_until.pop(broker_key, None)
        return True, None

    def reset(self, broker: Optional[str] = None) -> None:
        """Reset circuit breaker state."""
        if broker:
            k = broker.lower().split(":")[0]
            self._consecutive_errors.pop(k, None)
            self._tripped_until.pop(k, None)
            self._last_error.pop(k, None)
        else:
            self._consecutive_errors = {}
            self._tripped_until = {}
            self._last_error = {}


# Global circuit breaker singleton
broker_circuit_breaker = BrokerCircuitBreaker()
