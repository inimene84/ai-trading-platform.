"""
In-process Pub/Sub Data Hub
Ported from FinceptTerminal DataHub.h / DataHub.cpp

Usage:
    from backend.services.data_hub import DataHub

    # Subscribe
    DataHub().subscribe("market:quote:BTCUSDT", lambda v: print(v))

    # Publish (from any thread/service)
    DataHub().publish("market:quote:BTCUSDT", {"price": 65000, "change": 1.2})

    # Peek cached value
    val = DataHub().peek("market:quote:BTCUSDT")
"""
from __future__ import annotations
import logging
import threading
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class DataHub:
    """
    Thread-safe in-memory pub/sub with optional per-topic TTL.
    """

    _instance: Optional[DataHub] = None
    _lock = threading.Lock()

    def __new__(cls) -> DataHub:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    obj = super().__new__(cls)
                    obj._subs: Dict[str, List[Callable[[Any], None]]] = defaultdict(list)
                    obj._cache: Dict[str, tuple] = {}      # topic -> (value, timestamp_ms)
                    obj._ttl_ms: Dict[str, int] = {}       # topic -> ttl override
                    obj._internal_lock = threading.RLock()
                    cls._instance = obj
        return cls._instance

    def subscribe(self, topic: str, callback: Callable[[Any], None], immediate: bool = True):
        """
        Subscribe to a topic. If a cached value exists and is fresh,
        deliver it immediately.
        """
        with self._internal_lock:
            self._subs[topic].append(callback)
            if immediate and topic in self._cache:
                val, ts = self._cache[topic]
                ttl = self._ttl_ms.get(topic, 30_000)
                if (time.time() * 1000 - ts) < ttl:
                    try:
                        callback(val)
                    except Exception:
                        pass

    def unsubscribe(self, topic: str, callback: Callable[[Any], None]):
        with self._internal_lock:
            if topic in self._subs:
                try:
                    self._subs[topic].remove(callback)
                except ValueError:
                    pass

    def publish(self, topic: str, value: Any, ttl_ms: Optional[int] = None):
        """
        Store value and fan out to all subscribers.
        Thread-safe.
        """
        with self._internal_lock:
            self._cache[topic] = (value, int(time.time() * 1000))
            if ttl_ms is not None:
                self._ttl_ms[topic] = ttl_ms
            subs = list(self._subs.get(topic, []))

        for cb in subs:
            try:
                cb(value)
            except Exception as e:
                logger.error(f"Subscriber error on {topic}: {e}")

    def peek(self, topic: str) -> Any:
        """Read cached value without subscribing. Returns None if stale/absent."""
        with self._internal_lock:
            if topic not in self._cache:
                return None
            val, ts = self._cache[topic]
            ttl = self._ttl_ms.get(topic, 30_000)
            if (time.time() * 1000 - ts) > ttl:
                return None
            return val

    def get_all_topics(self) -> List[str]:
        with self._internal_lock:
            return list(self._cache.keys())

    def stats(self) -> dict:
        with self._internal_lock:
            return {
                "topics": len(self._cache),
                "subscribers": {k: len(v) for k, v in self._subs.items()},
            }
