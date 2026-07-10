import pytest
from unittest.mock import AsyncMock, patch
from backend.services.risk_reviewer import review_trade_decision

@pytest.mark.asyncio
async def test_review_trade_decision_approve():
    with patch("backend.llm.router.call_llm_resilient", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = '{"approved": true, "reasoning": "Strong setup with clean support levels."}'
        approved, reasoning = await review_trade_decision(
            symbol="BTCUSDT",
            action="BUY",
            quantity=0.01,
            entry_price=90000.0,
            stop_loss=88000.0,
            take_profit=95000.0,
            confidence=0.8,
            funding_rate=0.0001,
            news_summary="Positive market sentiment."
        )
        assert approved is True
        assert "Strong setup" in reasoning

@pytest.mark.asyncio
async def test_review_trade_decision_veto():
    with patch("backend.llm.router.call_llm_resilient", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = '{"approved": false, "reasoning": "Funding rate is too high and news is bearish."}'
        approved, reasoning = await review_trade_decision(
            symbol="ETHUSDT",
            action="BUY",
            quantity=1.0,
            entry_price=3000.0,
            stop_loss=2950.0,
            take_profit=3100.0,
            confidence=0.5,
            funding_rate=0.0006,
            news_summary="Negative sentiment and high funding."
        )
        assert approved is False
        assert "Funding rate" in reasoning


@pytest.mark.asyncio
async def test_review_trade_decision_fail_open_on_garbage_response():
    with patch("backend.llm.router.call_llm_resilient", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = "I recommend rejecting this trade."

        approved, reasoning = await review_trade_decision(
            symbol="BTCUSDT",
            action="SELL",
            quantity=0.01,
            entry_price=90000.0,
            stop_loss=92000.0,
            take_profit=85000.0,
            confidence=0.85,
            funding_rate=0.0001,
            news_summary="Mixed news.",
        )
        assert approved is True
        assert "fail-open" in reasoning.lower()


@pytest.mark.asyncio
async def test_parse_reviewer_parses_markdown_json():
    from backend.services.risk_reviewer import _parse_reviewer_response

    approved, reasoning = _parse_reviewer_response(
        '```json\n{"approved": true, "reasoning": "Clean setup."}\n```'
    )
    assert approved is True
    assert reasoning == "Clean setup."


@pytest.mark.asyncio
async def test_parse_reviewer_extracts_json_from_prose():
    from backend.services.risk_reviewer import _parse_reviewer_response

    approved, reasoning = _parse_reviewer_response(
        'After review, here is my decision: {"approved": true, "reasoning": "Trend aligned."}'
    )
    assert approved is True
    assert "Trend aligned" in reasoning


@pytest.mark.asyncio
async def test_parse_reviewer_fail_open_on_plain_prose_veto():
    from backend.services.risk_reviewer import _parse_reviewer_response

    approved, reasoning = _parse_reviewer_response(
        "I cannot approve this trade due to excessive risk."
    )
    assert approved is True
    assert "fail-open" in reasoning.lower()


def _review_kwargs():
    return dict(
        symbol="BTCUSDT", action="BUY", quantity=0.01, entry_price=90000.0,
        stop_loss=88000.0, take_profit=95000.0, confidence=0.8,
        funding_rate=0.0001, news_summary="Mixed news.",
    )


@pytest.mark.asyncio
async def test_reviewer_outage_fails_closed_in_live_mode(monkeypatch):
    """LLM chain down in LIVE mode must veto, not silently approve."""
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.delenv("RISK_REVIEWER_FAIL_OPEN", raising=False)
    with patch("backend.llm.router.call_llm_resilient", new_callable=AsyncMock) as mock_call:
        mock_call.side_effect = RuntimeError("All LLM providers in the chain failed")
        approved, reasoning = await review_trade_decision(**_review_kwargs())
    assert approved is False
    assert "fail-closed" in reasoning.lower()


@pytest.mark.asyncio
async def test_reviewer_outage_fails_open_in_paper_mode(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.delenv("RISK_REVIEWER_FAIL_OPEN", raising=False)
    with patch("backend.llm.router.call_llm_resilient", new_callable=AsyncMock) as mock_call:
        mock_call.side_effect = RuntimeError("All LLM providers in the chain failed")
        approved, reasoning = await review_trade_decision(**_review_kwargs())
    assert approved is True
    assert "fail-open" in reasoning.lower()


@pytest.mark.asyncio
async def test_reviewer_outage_env_override_fails_open_in_live(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("RISK_REVIEWER_FAIL_OPEN", "true")
    with patch("backend.llm.router.call_llm_resilient", new_callable=AsyncMock) as mock_call:
        mock_call.side_effect = RuntimeError("All LLM providers in the chain failed")
        approved, reasoning = await review_trade_decision(**_review_kwargs())
    assert approved is True
    assert "fail-open" in reasoning.lower()


def test_unparseable_reviewer_response_fails_closed_in_live(monkeypatch):
    from backend.services.risk_reviewer import _parse_reviewer_response

    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.delenv("RISK_REVIEWER_FAIL_OPEN", raising=False)
    approved, reasoning = _parse_reviewer_response(
        "I think this might be okay, but I cannot return JSON."
    )
    assert approved is False
    assert "fail-closed" in reasoning.lower()


def test_empty_reviewer_response_fails_closed_in_live(monkeypatch):
    from backend.services.risk_reviewer import _parse_reviewer_response

    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.delenv("RISK_REVIEWER_FAIL_OPEN", raising=False)
    approved, reasoning = _parse_reviewer_response("")
    assert approved is False
    assert "fail-closed" in reasoning.lower()
