"""
InfluxDB Sentiment & Alert Reader
Reads aggregated social sentiment and market alerts written by n8n workflows.
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "sentiment")
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


class SentimentReader:
    """Read social sentiment and market alerts from InfluxDB."""

    def get_sentiment(self, symbol: str, lookback_minutes: int = 60) -> Optional[Dict]:
        """
        Fetch aggregated sentiment for a symbol.
        Returns dict with keys: direction, confidence, sentiment_score, article_count, sources
        """
        client = _get_influx_client()
        if not client:
            return None

        try:
            query_api = client.query_api()
            # Normalize symbol for tag matching (remove USDT/PERP suffixes if needed)
            base = symbol.replace("USDT", "").replace("PERP", "").upper()

            flux = f'''
            from(bucket: "{_INFLUXDB_BUCKET}")
                |> range(start: -{lookback_minutes}m)
                |> filter(fn: (r) => r._measurement == "sentiment")
                |> filter(fn: (r) => r.symbol == "{base}" or r.symbol == "{symbol}")
                |> filter(fn: (r) => r._field == "score" or r._field == "confidence" or r._field == "count")
                |> aggregateWindow(every: {max(lookback_minutes, 1)}m, fn: mean, createEmpty: false)
                |> yield(name: "mean")
            '''
            tables = query_api.query(flux)
            if not tables:
                return None

            score = 0.0
            confidence = 0.0
            count = 0
            for table in tables:
                for record in table.records:
                    field = record.get_field()
                    val = record.get_value()
                    if field == "score" and val is not None:
                        score = float(val)
                    elif field == "confidence" and val is not None:
                        confidence = float(val)
                    elif field == "count" and val is not None:
                        count = int(val)

            if score > 0.2:
                direction = "BUY"
            elif score < -0.2:
                direction = "SELL"
            else:
                direction = "NEUTRAL"

            return {
                "direction": direction,
                "confidence": confidence,
                "sentiment_score": score,
                "article_count": count,
                "sources": {"n8n_aggregate": score},
            }
        except Exception as e:
            logger.warning(f"Sentiment query error for {symbol}: {e}")
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
                |> filter(fn: (r) => r.symbol == "{base}" or r.symbol == "{symbol}")
                |> filter(fn: (r) => r._field == "score" or r._field == "alert_type")
                |> last()
            '''
            tables = query_api.query(flux)
            alerts = []
            current = {}
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
                    "score": float(data.get("score", 0) or 0),
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
        """
        client = _get_influx_client()
        if not client:
            return None

        try:
            query_api = client.query_api()
            flux = f'''
            from(bucket: "{_INFLUXDB_BUCKET}")
                |> range(start: -{lookback_minutes}m)
                |> filter(fn: (r) => r._measurement == "global_sentiment")
                |> filter(fn: (r) => r._field == "index" or r._field == "value")
                |> last()
            '''
            tables = query_api.query(flux)
            if not tables:
                return None

            value = 50.0
            for table in tables:
                for record in table.records:
                    val = record.get_value()
                    if val is not None:
                        value = float(val)

            if value > 75:
                direction = "SELL"  # Extreme greed = contrarian sell
            elif value > 55:
                direction = "BUY"
            elif value < 25:
                direction = "BUY"  # Extreme fear = contrarian buy
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
