import pytest
from unittest.mock import AsyncMock, patch
from backend.services.risk_reviewer import review_trade_decision, fetch_news_summary

@pytest.mark.asyncio
async def test_review_trade_decision_approve():
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.is_success = True
        from unittest.mock import MagicMock
        mock_response.json = MagicMock(return_value={
            "choices": [{
                "message": {
                    "content": '{"approved": true, "reasoning": "Strong setup with clean support levels."}'
                }
            }]
        })
        mock_post.return_value = mock_response
        
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
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.is_success = True
        from unittest.mock import MagicMock
        mock_response.json = MagicMock(return_value={
            "choices": [{
                "message": {
                    "content": '{"approved": false, "reasoning": "Funding rate is too high and news is bearish."}'
                }
            }]
        })
        mock_post.return_value = mock_response
        
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
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.is_success = True
        from unittest.mock import MagicMock
        mock_response.json = MagicMock(return_value={
            "choices": [{"message": {"content": "I recommend rejecting this trade."}}]
        })
        mock_post.return_value = mock_response

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
async def test_review_trade_decision_parses_markdown_json():
    from backend.services.risk_reviewer import _parse_reviewer_response

    approved, reasoning = _parse_reviewer_response(
        '```json\n{"approved": true, "reasoning": "Clean setup."}\n```'
    )
    assert approved is True
    assert reasoning == "Clean setup."
