"""
Redis session context cache.

Fixes applied:
- P0-3: append_pair_to_context() writes both messages in one GET→modify→SET,
  preventing orphaned user-only cache entries.

Key pattern:  session:{session_id}:context
TTL:          30 minutes (configurable via SESSION_CONTEXT_TTL)
"""
import os
import json
import logging


import redis.asyncio as redis

logger = logging.getLogger("session_cache")

_REDIS_URL = None
_TTL = None
_pool: redis.Redis | None = None

MAX_CONTEXT_MESSAGES = 10


def _get_config():
    global _REDIS_URL, _TTL
    if _REDIS_URL is None:
        _REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _TTL = int(os.getenv("SESSION_CONTEXT_TTL", "1800"))


def _key(session_id: str) -> str:
    return f"session:{session_id}:context"


async def get_redis() -> redis.Redis:
    global _pool
    _get_config()
    if _pool is None:
        _pool = redis.from_url(_REDIS_URL, decode_responses=True)
    return _pool


async def close_redis():
    global _pool
    if _pool:
        await _pool.aclose()
        _pool = None


async def ping() -> bool:
    try:
        r = await get_redis()
        return await r.ping()
    except Exception as e:
        logger.warning("Redis ping failed: %s", e)
        return False


async def get_context(session_id: str) -> list[dict] | None:
    """Load session context. Returns None on miss OR Redis error (distinguished by log level)."""
    try:
        r = await get_redis()
        data = await r.get(_key(session_id))
        if data is None:
            logger.debug("Cache miss: session=%s", session_id)
            return None
        logger.debug("Cache hit: session=%s", session_id)
        return json.loads(data)
    except redis.RedisError as e:
        logger.warning("Redis unavailable on get_context: session=%s error=%s", session_id, e)
        return None
    except Exception as e:
        logger.warning("Unexpected error in get_context: session=%s error=%s", session_id, e)
        return None


async def set_context(session_id: str, messages: list[dict]):
    """Write session context with TTL. Truncates to MAX_CONTEXT_MESSAGES."""
    try:
        r = await get_redis()
        truncated = messages[-MAX_CONTEXT_MESSAGES:]
        clean = [
            {"role": m["role"], "content": m["content"]}
            for m in truncated
            if m.get("role") and m.get("content")
        ]
        await r.set(_key(session_id), json.dumps(clean, ensure_ascii=False), ex=_TTL)
        logger.debug("Cache set: session=%s messages=%d", session_id, len(clean))
    except redis.RedisError as e:
        logger.warning("Redis unavailable on set_context: session=%s error=%s", session_id, e)
    except Exception as e:
        logger.warning("Unexpected error in set_context: session=%s error=%s", session_id, e)


async def append_pair_to_context(
    session_id: str,
    user_content: str,
    assistant_content: str,
):
    """
    P0-3 fix: Append user + assistant messages in ONE GET→modify→SET cycle.
    Prevents orphaned user-only cache entries if the second write would fail.
    """
    try:
        existing = await get_context(session_id) or []
        existing.extend([
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ])
        await set_context(session_id, existing)
    except Exception as e:
        logger.warning("Failed to append pair: session=%s error=%s", session_id, e)


async def invalidate_context(session_id: str):
    """Remove a session's context from cache."""
    try:
        r = await get_redis()
        await r.delete(_key(session_id))
        logger.debug("Cache invalidated: session=%s", session_id)
    except redis.RedisError as e:
        logger.warning("Redis unavailable on invalidate: session=%s error=%s", session_id, e)
    except Exception as e:
        logger.warning("Unexpected error in invalidate: session=%s error=%s", session_id, e)
