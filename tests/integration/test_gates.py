"""Replay protection and rate limiting are atomic and fail closed."""

from __future__ import annotations

import pytest
from redis import Redis

from taxstamp.errors import DependencyUnavailable, RateLimited
from taxstamp.gates import RateLimiter, ReplayGuard
from taxstamp.runtime import Runtime

pytestmark = pytest.mark.integration


def test_nonce_can_only_be_claimed_once(runtime: Runtime) -> None:
    assert runtime.replay_guard.claim("device-1", "nonce-1")
    assert not runtime.replay_guard.claim("device-1", "nonce-1")
    assert runtime.replay_guard.claim("device-2", "nonce-1")


def test_rate_limiter_blocks_beyond_the_limit(runtime: Runtime) -> None:
    for _ in range(3):
        runtime.rate_limiter.enforce("principal-1", "bucket", 3)
    with pytest.raises(RateLimited):
        runtime.rate_limiter.enforce("principal-1", "bucket", 3)


def test_gates_fail_closed_when_redis_is_unavailable() -> None:
    unreachable = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2, socket_timeout=0.2)
    guard = ReplayGuard(unreachable, ttl_seconds=60)
    limiter = RateLimiter(unreachable, window_seconds=60)
    with pytest.raises(DependencyUnavailable):
        guard.claim("device", "nonce")
    with pytest.raises(DependencyUnavailable):
        limiter.enforce("principal", "bucket", 10)
