"""Periodic calendar + news ingest into the QuantumTrade backend.

Calendar sources (first that works):
  1. JBlanked News API when JBLANKED_API_KEY is set
  2. CALENDAR_JSON_URL (pre-normalized or ForexFactory-like JSON)

News: comma-separated NEWS_RSS_URLS (default Fed press RSS).
"""

from __future__ import annotations

import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.INFO, format="[scrapling-ingest] %(message)s")
log = logging.getLogger("scrapling-ingest")

BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://ai-trading-backend:8000").rstrip("/")
BACKEND_API_PREFIX = os.getenv("BACKEND_API_PREFIX", "/api").rstrip("/")
ADMIN_API_KEY = (os.getenv("ADMIN_API_KEY") or os.getenv("SCRAPLING_MCP_AUTH_TOKEN") or "").strip()
INTERVAL_SEC = int(os.getenv("SCRAPLING_INGEST_INTERVAL_SEC", "3600"))
JBLANKED_API_KEY = (os.getenv("JBLANKED_API_KEY") or "").strip()
JBLANKED_CALENDAR_URL = os.getenv(
    "JBLANKED_CALENDAR_URL",
    "https://www.jblanked.com/news/api/forex-factory/calendar/week/",
)
CALENDAR_JSON_URL = (os.getenv("CALENDAR_JSON_URL") or "").strip()
NEWS_RSS_URLS = os.getenv(
    "NEWS_RSS_URLS",
    "https://www.federalreserve.gov/feeds/press_all.xml",
)
USER_AGENT = "QuantumTrade-Scrapling/1.0 (+research sidecar)"


def _api(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    if BACKEND_API_PREFIX and not path.startswith(BACKEND_API_PREFIX + "/") and path != BACKEND_API_PREFIX:
        path = BACKEND_API_PREFIX + path
    return f"{BACKEND_BASE_URL}{path}"


def _headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if extra:
        headers.update(extra)
    if ADMIN_API_KEY:
        headers["Authorization"] = f"Bearer {ADMIN_API_KEY}"
        headers["X-API-Key"] = ADMIN_API_KEY
    return headers


def _http_json(url: str, *, method: str = "GET", payload: Optional[Any] = None, headers: Optional[Dict[str, str]] = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req_headers = _headers({"Content-Type": "application/json"} if body is not None else None)
    if headers:
        req_headers.update(headers)
    request = Request(url, data=body, headers=req_headers, method=method)
    with urlopen(request, timeout=45) as response:  # noqa: S310 - URL is configured by operators
        raw = response.read()
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _http_text(url: str) -> str:
    request = Request(url, headers=_headers())
    with urlopen(request, timeout=45) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="replace")


def _normalize_impact(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"HIGH", "H", "3", "RED"}:
        return "HIGH"
    if text in {"MEDIUM", "MED", "M", "2", "ORANGE"}:
        return "MEDIUM"
    if text in {"LOW", "L", "1", "YELLOW"}:
        return "LOW"
    return "NONE" if not text else text


def _parse_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def calendar_event_from_raw(item: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    title = item.get("title") or item.get("Name") or item.get("name") or item.get("event")
    currency = item.get("currency") or item.get("Currency") or item.get("country") or item.get("Country")
    time_utc = _parse_time(
        item.get("time_utc")
        or item.get("date")
        or item.get("Date")
        or item.get("datetime")
        or item.get("time")
    )
    if not title or not currency or time_utc is None:
        return None
    return {
        "time_utc": time_utc.isoformat(),
        "currency": str(currency).strip().upper()[:8],
        "impact": _normalize_impact(item.get("impact") or item.get("Impact") or item.get("volatility")),
        "title": str(title).strip()[:300],
        "actual": _optional_str(item.get("actual") or item.get("Actual")),
        "forecast": _optional_str(item.get("forecast") or item.get("Forecast")),
        "previous": _optional_str(item.get("previous") or item.get("Previous")),
        "source": source,
    }


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_item_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("events", "data", "results", "calendar"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    return []


def load_calendar_events() -> List[Dict[str, Any]]:
    if JBLANKED_API_KEY:
        try:
            headers = {"Authorization": f"Api-Key {JBLANKED_API_KEY}", "Content-Type": "application/json"}
            payload = _http_json(JBLANKED_CALENDAR_URL, headers=headers)
            events = [calendar_event_from_raw(item, "jblanked") for item in _as_item_list(payload)]
            found = [event for event in events if event]
            if found:
                return found
            log.warning("JBlanked returned no mappable events")
        except (HTTPError, URLError, json.JSONDecodeError) as exc:
            log.warning("JBlanked calendar fetch failed: %s", exc)

    if CALENDAR_JSON_URL:
        payload = _http_json(CALENDAR_JSON_URL)
        events = [calendar_event_from_raw(item, "scrapling") for item in _as_item_list(payload)]
        return [event for event in events if event]

    log.info("no calendar source configured (set JBLANKED_API_KEY or CALENDAR_JSON_URL)")
    return []


def load_news_articles() -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    urls = [part.strip() for part in NEWS_RSS_URLS.split(",") if part.strip()]
    for feed_url in urls:
        try:
            xml_text = _http_text(feed_url)
            root = ET.fromstring(xml_text)
        except (HTTPError, URLError, ET.ParseError) as exc:
            log.warning("rss fetch failed %s: %s", feed_url, exc)
            continue
        items = root.findall(".//item")
        for item in items[:20]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            published = item.findtext("pubDate")
            if not title:
                continue
            articles.append(
                {
                    "source": "scrapling-rss",
                    "external_id": link or title,
                    "title": title[:400],
                    "description": description[:2000] or None,
                    "url": link or None,
                    "published_at": published,
                    "auto_score": True,
                }
            )
    return articles


def post_calendar(events: Iterable[Dict[str, Any]]) -> None:
    batch = list(events)
    if not batch:
        return
    result = _http_json(_api("/calendar/batch"), method="POST", payload={"events": batch})
    log.info("calendar ingest count=%s", (result or {}).get("count", len(batch)))


def post_news(articles: Iterable[Dict[str, Any]]) -> None:
    count = 0
    for article in articles:
        _http_json(_api("/sentiment/ingest"), method="POST", payload=article)
        count += 1
    if count:
        log.info("news ingest count=%s", count)


def run_once() -> None:
    try:
        post_calendar(load_calendar_events())
    except Exception:
        log.exception("calendar ingest failed")
    try:
        post_news(load_news_articles())
    except Exception:
        log.exception("news ingest failed")


def main() -> None:
    log.info("starting ingest loop every %ss backend=%s", INTERVAL_SEC, BACKEND_BASE_URL)
    for attempt in range(1, 7):
        try:
            run_once()
            break
        except Exception:
            log.exception("startup ingest attempt %s failed", attempt)
            time.sleep(10 * attempt)
    while True:
        time.sleep(max(INTERVAL_SEC, 60))
        run_once()


if __name__ == "__main__":
    main()
