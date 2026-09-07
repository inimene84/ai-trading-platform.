from scrapling_sidecar.ingest import calendar_event_from_raw
from scrapling_sidecar.url_guard import is_public_http_url


def test_calendar_event_from_raw_maps_forexfactory_shape():
    event = calendar_event_from_raw(
        {
            "title": "Nonfarm Payrolls",
            "country": "USD",
            "date": "2026-09-08T12:30:00Z",
            "impact": "High",
            "forecast": "165K",
            "previous": "142K",
        },
        "scrapling",
    )
    assert event is not None
    assert event["currency"] == "USD"
    assert event["impact"] == "HIGH"
    assert event["title"] == "Nonfarm Payrolls"
    assert event["forecast"] == "165K"
    assert event["source"] == "scrapling"


def test_calendar_event_from_raw_maps_jblanked_shape():
    event = calendar_event_from_raw(
        {
            "Name": "CPI y/y",
            "Currency": "EUR",
            "Date": "2026-09-08T09:00:00+00:00",
            "Impact": "Medium",
            "Actual": "2.1%",
            "Forecast": "2.0%",
            "Previous": "2.2%",
        },
        "jblanked",
    )
    assert event is not None
    assert event["currency"] == "EUR"
    assert event["impact"] == "MEDIUM"
    assert event["actual"] == "2.1%"
    assert event["source"] == "jblanked"


def test_calendar_event_from_raw_rejects_incomplete():
    assert calendar_event_from_raw({"title": "CPI"}, "scrapling") is None


def test_url_guard_allows_public_and_blocks_internal():
    assert is_public_http_url("https://example.com/news")
    assert not is_public_http_url("http://127.0.0.1/health")
    assert not is_public_http_url("http://ai-trading-backend:8000/trading/portfolio")
    assert not is_public_http_url("http://169.254.169.254/latest/meta-data")
    assert not is_public_http_url("file:///etc/passwd")
