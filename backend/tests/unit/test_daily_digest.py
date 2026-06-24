import pytest
from unittest.mock import AsyncMock, patch
from backend.services.daily_digest import generate_digest_text

@pytest.mark.asyncio
async def test_generate_digest_text_with_api_key():
    stats = {
        "total_pnl": 150.50,
        "wins": 3,
        "losses": 1,
        "closed_count": 4,
        "open_positions_count": 2,
        "open_exposure": 500.0,
        "total_signals": 10,
        "vetoed_signals": 2,
        "equity": 10250.0,
        "cash": 9750.0,
    }
    with patch("backend.services.daily_digest.call_llm_resilient", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
        
        digest = await generate_digest_text(stats)
        assert "Trading Daily Digest" in digest
        assert "Line 1" in digest

@pytest.mark.asyncio
async def test_generate_digest_text_fallback():
    stats = {
        "total_pnl": 150.50,
        "wins": 3,
        "losses": 1,
        "closed_count": 4,
        "open_positions_count": 2,
        "open_exposure": 500.0,
        "total_signals": 10,
        "vetoed_signals": 2,
        "equity": 10250.0,
        "cash": 9750.0,
    }
    with patch("backend.services.daily_digest.call_llm_resilient", new_callable=AsyncMock) as mock_call:
        mock_call.side_effect = Exception("API down")
        digest = await generate_digest_text(stats)
        assert "Trading Daily Digest" in digest
        assert "Net PnL" in digest
        assert "$150.50" in digest
