"""
Qdrant Vector Store Client
Manages crypto news archive with semantic search capabilities.
"""

import os
import logging
import time
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

try:
    from qdrant_client import QdrantClient, AsyncQdrantClient
    from qdrant_client.models import (
        VectorParams,
        Distance,
        PointStruct,
        Filter,
        FieldCondition,
        MatchValue,
    )
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False
    logger.warning("qdrant-client not installed – news archive disabled")


class QdrantNewsClient:
    """Async Qdrant client for crypto news vector storage."""

    def __init__(self):
        self.url = os.getenv("QDRANT_URL", "http://qdrant:6333")
        self.api_key = os.getenv("QDRANT_API_KEY", "")
        self.collection_name = os.getenv("QDRANT_COLLECTION_CRYPTO_NEWS", "crypto-news")
        self.vector_size = int(os.getenv("QDRANT_VECTOR_SIZE", "1536"))

        if not QDRANT_AVAILABLE:
            self._client = None
            logger.warning("qdrant-client not installed – news archive disabled")
            return

        self._client = AsyncQdrantClient(
            url=self.url,
            api_key=self.api_key if self.api_key else None,
        )

    async def ensure_collection(self) -> bool:
        """Create the crypto-news collection if it doesn't exist."""
        if not self._client:
            return False

        try:
            # Check if collection exists
            collections = await self._client.get_collections()
            exists = any(c.name == self.collection_name for c in collections.collections)

            if not exists:
                await self._client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.vector_size,
                        distance=Distance.COSINE,
                    ),
                )
                logger.info(f"Created Qdrant collection: {self.collection_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to ensure collection: {e}")
            return False

    async def store_news_article(
        self,
        title: str,
        content: str,
        source: str,
        url: str,
        published_at: str,
        sentiment: float,
        symbols: List[str],
        embedding: List[float],
    ) -> Optional[int]:  # Changed to int for Qdrant compatibility
        """Store a news article with its embedding. Returns point ID."""
        if not self._client:
            return None

        try:
            await self.ensure_collection()

            point_id = int(time.time() * 1000000)  # Microsecond precision for uniqueness
            point = PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "title": title[:500],
                    "content": content[:2000],
                    "source": source,
                    "url": url,
                    "published_at": published_at,
                    "sentiment": sentiment,
                    "symbols": symbols,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )

            await self._client.upsert(
                collection_name=self.collection_name,
                points=[point],
            )
            return point_id
        except Exception as e:
            logger.error(f"Failed to store news article: {e}")
            # Re-raise to see actual error
            raise

    async def search_news(
        self,
        query: str,
        limit: int = 10,
        query_embedding: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        """Semantic search for news articles. Supply query_embedding or leave to embed query."""
        if not self._client:
            return []

        try:
            # For now, use simple keyword search if no embedding provided
            # In production, call embedding model here
            results = await self._client.search(
                collection_name=self.collection_name,
                query_filter=Filter(
                    must=[
                        FieldCondition(
                            key="content",
                            match=MatchValue(value=query.lower()),
                        )
                    ]
                ) if query_embedding is None else None,
                query_vector=query_embedding,
                limit=limit,
            )

            return [
                {
                    "id": r.id,
                    "score": r.score,
                    "title": r.payload.get("title", ""),
                    "content": r.payload.get("content", ""),
                    "source": r.payload.get("source", ""),
                    "url": r.payload.get("url", ""),
                    "published_at": r.payload.get("published_at", ""),
                    "sentiment": r.payload.get("sentiment", 0),
                    "symbols": r.payload.get("symbols", []),
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    async def get_news_history(self, page: int = 1, limit: int = 20) -> List[Dict[str, Any]]:
        """Get paginated news history (non-semantic)."""
        if not self._client:
            return []

        try:
            offset = (page - 1) * limit
            results = await self._client.scroll(
                collection_name=self.collection_name,
                limit=limit,
                offset=offset,
                with_vectors=False,
                with_payload=True,
            )

            return [
                {
                    "id": pt.id,
                    "title": pt.payload.get("title", ""),
                    "content": pt.payload.get("content", ""),
                    "source": pt.payload.get("source", ""),
                    "url": pt.payload.get("url", ""),
                    "published_at": pt.payload.get("published_at", ""),
                    "sentiment": pt.payload.get("sentiment", 0),
                    "symbols": pt.payload.get("symbols", []),
                    "created_at": pt.payload.get("created_at", ""),
                }
                for pt in results[0]
            ]
        except Exception as e:
            logger.error(f"History fetch failed: {e}")
            return []

    async def get_collection_info(self) -> Dict[str, Any]:
        """Get collection information for status endpoint."""
        if not self._client:
            return {"name": self.collection_name, "points_count": 0, "vector_size": self.vector_size}
        try:
            collections = await self._client.get_collections()
            for c in collections.collections:
                if c.name == self.collection_name:
                    # Get collection info for points count
                    info = await self._client.get_collection(collection_name=self.collection_name)
                    return {
                        "name": c.name,
                        "points_count": getattr(info, 'points_count', 0),
                        "vector_size": self.vector_size,
                    }
            return {"name": self.collection_name, "points_count": 0, "vector_size": self.vector_size}
        except Exception as e:
            logger.error(f"Failed to get collection info: {e}")
            return {"name": self.collection_name, "points_count": 0, "vector_size": self.vector_size}


# Singleton instance
qdrant = QdrantNewsClient()