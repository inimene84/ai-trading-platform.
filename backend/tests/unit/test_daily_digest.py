import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.services.daily_digest import generate_digest_text, send_telegram_message

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
    with patch("backend.services.daily_digest.get_api_key", return_value="dummy_key"), \
         patch("backend.services.daily_digest.pick_model") as mock_pick, \
         patch("httpx.AsyncClient.post") as mock_post:
         
        mock_model = MagicMock()
        mock_model.name = "claude-sonnet"
        mock_model.base_url = "http://litellm:4000/v1"
        mock_model.temperature = 0.3
        mock_model.max_tokens = 512
        mock_pick.return_value = mock_model
        
        mock_response = AsyncMock()
        mock_response.is_success = True
        mock_response.json = MagicMock(return_value={
            "choices": [{
                "message": {
                    "content": "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
                }
            }]
        })
        mock_post.return_value = mock_response
        
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
    with patch("backend.services.daily_digest.get_api_key", return_value=""):
        digest = await generate_digest_text(stats)
        assert "Trading Daily Digest" in digest
        assert "Net PnL" in digest
        assert "$150.50" in digest
