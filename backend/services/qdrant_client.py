"""
Qdrant Vector Store Client
Manages crypto news archive with semantic search capabilities.
"""

import os
import logging
import time
import asyncio
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
    
    QdrantClient = None
    AsyncQdrantClient = None
    
    class VectorParams:
        def __init__(self, size: int, distance: Any):
            self.size = size
            self.distance = distance
            
    class Distance:
        COSINE = "Cosine"
        EUCLID = "Euclid"
        DOT = "Dot"
        
    class PointStruct:
        def __init__(self, id: Any, vector: Any, payload: Any):
            self.id = id
            self.vector = vector
            self.payload = payload
            
    class Filter:
        def __init__(self, must: List[Any] = None, should: List[Any] = None, must_not: List[Any] = None):
            self.must = must
            self.should = should
            self.must_not = must_not
            
    class FieldCondition:
        def __init__(self, key: str, match: Any):
            self.key = key
            self.match = match
            
    class MatchValue:
        def __init__(self, value: Any):
            self.value = value


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
            timeout=5.0,
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

        max_retries = 3
        backoff = 0.5
        for attempt in range(max_retries):
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
                logger.warning(
                    f"Qdrant store_news_article failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                else:
                    logger.error(f"Failed to store news article after {max_retries} attempts: {e}")
                    raise

    async def search_news(
        self,
        query: str,
        limit: int = 10,
        query_embedding: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        """Semantic search for news articles. Supply query_embedding for vector search,
        or omit for a payload text scroll (non-semantic fallback)."""
        if not self._client:
            return []

        try:
            if query_embedding is not None:
                # True semantic vector search
                results = await self._client.search(
                    collection_name=self.collection_name,
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
            else:
                # Fallback: scroll all and filter client-side by keyword
                all_points, _ = await self._client.scroll(
                    collection_name=self.collection_name,
                    limit=200,
                    with_vectors=False,
                    with_payload=True,
                )
                q = query.lower()
                matches = [
                    {
                        "id": pt.id,
                        "score": 0.0,
                        "title": pt.payload.get("title", ""),
                        "content": pt.payload.get("content", ""),
                        "source": pt.payload.get("source", ""),
                        "url": pt.payload.get("url", ""),
                        "published_at": pt.payload.get("published_at", ""),
                        "sentiment": pt.payload.get("sentiment", 0),
                        "symbols": pt.payload.get("symbols", []),
                    }
                    for pt in all_points
                    if q in pt.payload.get("title", "").lower()
                    or q in pt.payload.get("content", "").lower()
                ]
                return matches[:limit]
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    async def search_content(self, keywords: List[str], limit: int = 5, scan: int = 200) -> List[Dict[str, Any]]:
        """Return archived documents whose `content` mentions any keyword.

        Handles the LangChain-style `{content, metadata}` payload that the n8n
        news workflow actually writes (the structured title/sentiment schema is
        not populated). Used by the trading opinion layer as a news signal.
        """
        if not self._client:
            return []
        try:
            all_points, _ = await self._client.scroll(
                collection_name=self.collection_name,
                limit=scan,
                with_vectors=False,
                with_payload=True,
            )
            kws = [k.lower() for k in keywords if k]
            out: List[Dict[str, Any]] = []
            for pt in all_points:
                pl = pt.payload or {}
                content = pl.get("content") or pl.get("text") or pl.get("page_content") or ""
                if not content:
                    continue
                if not kws or any(k in content.lower() for k in kws):
                    out.append({"content": content, "metadata": pl.get("metadata", {})})
            return out[:limit]
        except Exception as e:
            logger.error(f"search_content failed: {e}")
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