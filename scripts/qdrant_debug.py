#!/usr/bin/env python3
"""Debug Qdrant connectivity from backend perspective"""
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()

print(f"QDRANT_URL: {os.getenv('QDRANT_URL')}")
print(f"QDRANT_COLLECTION_CRYPTO_NEWS: {os.getenv('QDRANT_COLLECTION_CRYPTO_NEWS')}")

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct

async def test():
    embedding = [0.1] * 1536
    import time
    point_id = f"test_{int(time.time() * 1000)}"
    
    client = AsyncQdrantClient(url="http://vps-qdrant:6333", api_key=None)
    
    # Try to upsert directly
    try:
        result = await client.upsert(
            collection_name="crypto-news",
            points=[PointStruct(
                id=point_id,
                vector=embedding,
                payload={"title": "Test", "content": "Test"}
            )]
        )
        print(f"Upsert result: {result}")
    except Exception as e:
        print(f"Upsert error: {e}")
    
    # Check points count
    try:
        info = await client.get_collection(collection_name="crypto-news")
        print(f"Points count: {info.points_count}")
    except Exception as e:
        print(f"Get collection error: {e}")

if __name__ == "__main__":
    asyncio.run(test())