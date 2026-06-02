import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi import HTTPException

from backend.services.influxdb_writer import InfluxDBWriter
from backend.services.qdrant_client import QdrantNewsClient
from backend.routes.news import archive_news, NewsArchivePayload


@pytest.mark.anyio
async def test_influxdb_writer_retry_on_failure():
    writer = InfluxDBWriter()
    writer._enabled = True  # force enabled for test
    
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await writer.write_signal(
                symbol="BTCUSDT",
                direction="BUY",
                confidence=0.8,
                entry_price=50000.0,
                stop_loss=49000.0,
                take_profit=52000.0,
            )
            
            # Assert called 3 times due to retries
            assert mock_post.call_count == 3
            # Assert backoff sleep was called twice
            assert mock_sleep.call_count == 2


@pytest.mark.anyio
async def test_qdrant_client_retry_and_fail():
    client = QdrantNewsClient()
    # Mock client and ensure_collection
    client._client = AsyncMock()
    client.ensure_collection = AsyncMock(return_value=True)
    
    # Mock upsert to raise Exception
    client._client.upsert = AsyncMock(side_effect=Exception("Qdrant connection refused"))
    
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(Exception, match="Qdrant connection refused"):
            await client.store_news_article(
                title="title",
                content="content",
                source="source",
                url="url",
                published_at="2026-06-03",
                sentiment=0.5,
                symbols=["BTC"],
                embedding=[0.1] * 1536,
            )
        
        # Verify upsert tried 3 times
        assert client._client.upsert.call_count == 3
        # Verify sleep called 2 times
        assert mock_sleep.call_count == 2


@pytest.mark.anyio
async def test_news_route_returns_503_on_qdrant_error():
    payload = NewsArchivePayload(
        title="Test",
        content="Test Content",
        source="test",
        url="http://test.com",
        published_at="2026-06-03",
        sentiment=0.5,
        symbols=["BTC"],
        embedding=[0.1] * 1536
    )
    
    # Mock qdrant.store_news_article to raise a connection-like exception
    with patch("backend.services.qdrant_client.qdrant.store_news_article", new_callable=AsyncMock, side_effect=Exception("Connection refused or Qdrant offline")):
        with pytest.raises(HTTPException) as exc_info:
            await archive_news(payload)
        
        assert exc_info.value.status_code == 503
        assert "Qdrant vector database is unreachable" in exc_info.value.detail
