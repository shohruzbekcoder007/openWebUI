"""Simple rate limiting (in-memory or Redis-backed)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from app.utils.logging import get_logger

logger = get_logger(__name__)


class RateLimiter:
    """Sliding-window rate limiter keyed by API key or client id."""

    def __init__(self, requests_per_minute: int = 120) -> None:
        self.rpm = max(0, requests_per_minute)
        self._windows: Dict[str, Deque[float]] = defaultdict(deque)
        self._redis = None
        self._use_redis = False

    async def attach_redis(self, redis_client) -> None:
        """Optionally use Redis for multi-instance deployments."""
        self._redis = redis_client
        self._use_redis = redis_client is not None
        if self._use_redis:
            logger.info("rate_limiter_redis_enabled")

    def enabled(self) -> bool:
        return self.rpm > 0

    async def check(self, key: str) -> tuple[bool, int]:
        """Return (allowed, remaining).

        If rate limiting is disabled, always allows.
        """
        if not self.enabled():
            return True, -1

        if self._use_redis and self._redis is not None:
            return await self._check_redis(key)
        return self._check_memory(key)

    def _check_memory(self, key: str) -> tuple[bool, int]:
        now = time.time()
        window_start = now - 60.0
        q = self._windows[key]

        while q and q[0] < window_start:
            q.popleft()

        if len(q) >= self.rpm:
            return False, 0

        q.append(now)
        remaining = max(0, self.rpm - len(q))
        return True, remaining

    async def _check_redis(self, key: str) -> tuple[bool, int]:
        """Redis fixed-window counter using INCR + EXPIRE."""
        redis_key = f"rl:{key}:{int(time.time() // 60)}"
        try:
            count = await self._redis.incr(redis_key)
            if count == 1:
                await self._redis.expire(redis_key, 70)
            remaining = max(0, self.rpm - int(count))
            allowed = int(count) <= self.rpm
            return allowed, remaining
        except Exception as exc:
            logger.warning("rate_limit_redis_fallback", error=str(exc))
            return self._check_memory(key)


rate_limiter = RateLimiter()
