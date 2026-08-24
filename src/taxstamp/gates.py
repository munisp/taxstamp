"""Redis-backed replay protection and rate limiting.

Both gates fail closed: if Redis is unreachable the request is refused rather than
admitted, because admitting it would allow unbounded signature replay.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis import Redis
from redis.exceptions import RedisError

from taxstamp.errors import DependencyUnavailable, RateLimited


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    limit: int


class ReplayGuard:
    """Single-use nonce registry with a bounded TTL."""

    def __init__(self, redis: Redis, *, ttl_seconds: int, namespace: str = "nonce") -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        self._namespace = namespace

    def claim(self, device_id: str, nonce: str) -> bool:
        """Atomically claim a nonce. Returns False when it has already been used."""
        key = f"{self._namespace}:{device_id}:{nonce}"
        try:
            created = self._redis.set(key, "1", nx=True, ex=self._ttl)
        except RedisError as exc:
            raise DependencyUnavailable("replay protection store is unavailable") from exc
        return bool(created)


class RateLimiter:
    """Fixed-window counter per (principal, bucket)."""

    def __init__(self, redis: Redis, *, window_seconds: int, namespace: str = "rl") -> None:
        self._redis = redis
        self._window = window_seconds
        self._namespace = namespace

    def check(self, subject: str, bucket: str, limit: int) -> RateLimitDecision:
        key = f"{self._namespace}:{bucket}:{subject}"
        try:
            pipeline = self._redis.pipeline()
            pipeline.incr(key, 1)
            pipeline.expire(key, self._window, nx=True)
            # redis-py does not annotate Pipeline.execute
            count_raw, _ = pipeline.execute()  # type: ignore[no-untyped-call]
        except RedisError as exc:
            raise DependencyUnavailable("rate limit store is unavailable") from exc
        count = int(count_raw)
        return RateLimitDecision(allowed=count <= limit, remaining=max(limit - count, 0), limit=limit)

    def enforce(self, subject: str, bucket: str, limit: int) -> None:
        decision = self.check(subject, bucket, limit)
        if not decision.allowed:
            raise RateLimited(f"rate limit of {limit} requests per window exceeded")
