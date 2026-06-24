from backend.services.market_alerts import derive_alert_points


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
