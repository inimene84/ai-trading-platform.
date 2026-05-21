"""
InfluxDB Sentiment & Alert Reader
Reads aggregated social sentiment and market alerts written by n8n workflows.

FIXES vs v1:
  - Use `last()` on the latest window instead of mean-averaging across windows
    (mean was diluting BULLISH signals with old NEUTRAL ones)
  - For per-symbol queries: fall back to CRYPTO/BTC tag when symbol has no data
    (n8n only writes BTC + CRYPTO tags; ETH/SOL/etc. all fell back gracefully)
  - Use the `direction` tag directly from InfluxDB instead of re-deriving from score
    (avoids 0.2 threshold dead-zone killing moderate signals)
  - confidence is read from field, not derived — keeps the n8n-computed value
"""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_INFLUXDB_BUCKET = os.getenv("INFLUXDB_SENTIMENT_BUCKET", os.getenv("INFLUXDB_BUCKET", "news-sentiment"))
_INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "-")
_INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
_INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "")


def _get_influx_client():
    """Lazy-load InfluxDB client."""
    try:
        from influxdb_client import InfluxDBClient
        client = InfluxDBClient(
            url=_INFLUXDB_URL,
            token=_INFLUXDB_TOKEN,
            org=_INFLUXDB_ORG,
        )
        return client
    except Exception as e:
        logger.warning(f"InfluxDB client init failed: {e}")
        return None


def _parse_direction_tag(tables) -> Optional[str]:
    """Extract the direction tag from the most recent record."""
    for table in tables:
        for record in table.records:
            d = record.values.get("direction", "")
            if d:
                return d.upper()
    return None


class SentimentReader:
    """Read social sentiment and market alerts from InfluxDB."""

    def _query_symbol_sentiment(self, client, symbol_tag: str, lookback_minutes: int) -> Optional[Dict]:
        """
        Inner query for a specific symbol tag.
        Picks the MOST RECENT window of records (by _time) across all direction-tagged groups.
        Returns parsed dict or None if no data.
        """
        try:
            query_api = client.query_api()
            # Sort descending and take first 3 records (all fields of latest timestamp)
            # This avoids the per-direction-group grouping issue where last() returns one
            # row per direction value instead of the most recent row overall.
            flux = f'''
            from(bucket: "{_INFLUXDB_BUCKET}")
                |> range(start: -{lookback_minutes}m)
                |> filter(fn: (r) => r._measurement == "news_sentiment")
                |> filter(fn: (r) => r.symbol == "{symbol_tag}")
                |> filter(fn: (r) => r._field == "sentiment_score" or r._field == "confidence" or r._field == "impact_score")
                |> group()
                |> sort(columns: ["_time"], desc: true)
                |> limit(n: 6)
            '''
            tables = query_api.query(flux)
            if not tables:
                return None

            # Collect all records, keyed by timestamp; pick the most recent timestamp's group
            from collections import defaultdict
            by_time: dict = defaultdict(dict)
            for table in tables:
                for record in table.records:
                    ts = record.get_time()
                    field = record.get_field()
                    val = record.get_value()
                    direction_tag = record.values.get("direction", "")
                    if ts not in by_time:
                        by_time[ts]["direction"] = direction_tag
                    if val is not None:
                        by_time[ts][field] = float(val)

            if not by_time:
                return None

            # Use most recent timestamp
            latest = max(by_time.keys())
            data = by_time[latest]

            score = data.get("sentiment_score")
            confidence = data.get("confidence")
            impact = data.get("impact_score")
            direction_tag = (data.get("direction") or "").upper()

            if score is None and confidence is None:
                return None

            # Use the direction tag stored by n8n directly
            if direction_tag in ("BULLISH", "BEARISH", "NEUTRAL", "BUY", "SELL"):
                direction = direction_tag
            else:
                s = score or 0.0
                if s > 0.15:
                    direction = "BULLISH"
                elif s < -0.15:
                    direction = "BEARISH"
                else:
                    direction = "NEUTRAL"

            signal_map = {"BULLISH": "BUY", "BUY": "BUY", "BEARISH": "SELL", "SELL": "SELL", "NEUTRAL": "NEUTRAL"}
            buy_sell = signal_map.get(direction, "NEUTRAL")

            return {
                "direction": buy_sell,
                "confidence": confidence if confidence is not None else 0.0,
                "sentiment_score": score if score is not None else 0.0,
                "article_count": int((impact or 0) * 10),
                "sources": {"n8n_aggregate": score or 0.0},
            }
        except Exception as e:
            logger.warning(f"Sentiment inner query error for {symbol_tag}: {e}")
            return None

    def get_sentiment(self, symbol: str, lookback_minutes: int = 60) -> Optional[Dict]:
        """
        Fetch aggregated sentiment for a symbol.
        Falls back to BTC data then CRYPTO if symbol-specific data missing.
        Returns dict with keys: direction, confidence, sentiment_score, article_count, sources
        """
        client = _get_influx_client()
        if not client:
            return None

        try:
            base = symbol.replace("USDT", "").replace("PERP", "").upper()

            # Try exact symbol first, then fallback chain
            for tag in [base, symbol, "BTC", "CRYPTO"]:
                result = self._query_symbol_sentiment(client, tag, lookback_minutes)
                if result is not None:
                    if tag not in (base, symbol):
                        logger.debug(f"Sentiment fallback for {symbol}: using {tag} data")
                        # Reduce confidence slightly since it's proxy data
                        result["confidence"] = round(result["confidence"] * 0.7, 3)
                        result["sources"]["proxy"] = tag
                    return result

            return None
        finally:
            client.close()

    def get_market_alerts(self, symbol: str, lookback_minutes: int = 60) -> List[Dict]:
        """
        Fetch market alerts (pumps, dumps, whale moves, trending).
        Returns list of alert dicts.
        """
        client = _get_influx_client()
        if not client:
            return []

        try:
            query_api = client.query_api()
            base = symbol.replace("USDT", "").replace("PERP", "").upper()

            flux = f'''
            from(bucket: "{_INFLUXDB_BUCKET}")
                |> range(start: -{lookback_minutes}m)
                |> filter(fn: (r) => r._measurement == "market_alert")
                |> filter(fn: (r) => r.symbol == "{base}" or r.symbol == "{symbol}" or r.symbol == "CRYPTO")
                |> filter(fn: (r) => r._field == "score" or r._field == "impact_score")
                |> last()
            '''
            tables = query_api.query(flux)
            alerts = []
            current: Dict[str, Dict] = {}
            for table in tables:
                for record in table.records:
                    field = record.get_field()
                    val = record.get_value()
                    tag = record.values.get("alert_type", "unknown")
                    if tag not in current:
                        current[tag] = {}
                    current[tag][field] = val

            for atype, data in current.items():
                alerts.append({
                    "alert_type": atype,
                    "score": float(data.get("score", data.get("impact_score", 0)) or 0),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            return alerts
        except Exception as e:
            logger.warning(f"Market alert query error for {symbol}: {e}")
            return []
        finally:
            client.close()

    def get_global_sentiment(self, lookback_minutes: int = 60) -> Optional[Dict]:
        """
        Fetch global Fear & Greed or aggregate market sentiment.
        Falls back to CRYPTO tag from news_sentiment if global_sentiment bucket is empty.
        """
        client = _get_influx_client()
        if not client:
            return None

        try:
            query_api = client.query_api()

            # Try dedicated global_sentiment measurement first
            flux = f'''
            from(bucket: "{_INFLUXDB_BUCKET}")
                |> range(start: -{lookback_minutes}m)
                |> filter(fn: (r) => r._measurement == "global_sentiment")
                |> filter(fn: (r) => r._field == "index" or r._field == "value")
                |> last()
            '''
            tables = query_api.query(flux)
            value = None
            for table in tables:
                for record in table.records:
                    val = record.get_value()
                    if val is not None:
                        value = float(val)

            # Fallback: use CRYPTO tag from news_sentiment as global proxy
            if value is None:
                result = self._query_symbol_sentiment(client, "CRYPTO", lookback_minutes)
                if result:
                    # sentiment_score is -1..+1; convert to 0..100 index
                    score = result.get("sentiment_score", 0.0)
                    value = 50.0 + (score * 50.0)  # 0=extreme fear, 100=extreme greed

            if value is None:
                return None

            if value > 75:
                direction = "SELL"   # Extreme greed = contrarian sell
            elif value > 55:
                direction = "BUY"
            elif value < 25:
                direction = "BUY"    # Extreme fear = contrarian buy
            elif value < 45:
                direction = "SELL"
            else:
                direction = "NEUTRAL"

            return {
                "direction": direction,
                "confidence": abs(value - 50) / 50,
                "sentiment_score": (value - 50) / 50,
                "index": value,
                "source": "fear_greed",
            }
        except Exception as e:
            logger.warning(f"Global sentiment query error: {e}")
            return None
        finally:
            client.close()


# Module-level singleton
sentiment_reader = SentimentReader()
