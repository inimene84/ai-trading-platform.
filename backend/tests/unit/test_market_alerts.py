from backend.services.market_alerts import build_fallback_output, derive_alert_points


def test_derive_alert_points_buy_risk_on():
    points = derive_alert_points(
        {"bias": "BUY", "marketMood": "RISK_ON", "confidence": 80}
    )
    types = {p[0] for p in points}
    assert "trending" in types
    assert all(score == 80 for _, score in points if _ == "trending")


def test_derive_alert_points_sell_risk_off():
    points = derive_alert_points(
        {"bias": "SELL", "marketMood": "RISK_OFF", "confidence": 65}
    )
    types = {p[0] for p in points}
    assert "dump" in types


def test_derive_alert_points_hold_neutral():
    points = derive_alert_points(
        {"bias": "HOLD", "marketMood": "NEUTRAL", "confidence": 40}
    )
    assert len(points) == 1
    assert points[0][0] == "neutral"


def test_build_fallback_output_extreme_fear():
    out = build_fallback_output(10, "Extreme Fear", headline_count=6)
    assert out["bias"] == "SELL"
    assert out["marketMood"] == "RISK_OFF"
    assert out["confidence"] >= 40


def test_build_fallback_output_extreme_greed():
    out = build_fallback_output(90, "Extreme Greed", headline_count=9)
    assert out["bias"] == "BUY"
    assert out["marketMood"] == "RISK_ON"
