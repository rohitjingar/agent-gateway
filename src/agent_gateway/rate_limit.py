"""Rate limiting via a token bucket.

Mental model (maps to a bucket that leaks): each client has a bucket of `capacity`
tokens that refills at `refill_per_sec`. Every tool call spends one token; an empty
bucket means 429. Bursts up to `capacity` are allowed, but the *sustained* rate can
never exceed the refill rate. It's the same shape as an API throttle you'd put in
front of any service — here the "client" is an agent identity.

Two implementations:
- RedisTokenBucket: atomic (Lua) so it's correct across many gateway workers.
- InMemoryTokenBucket: single-process, deterministic — used by tests and local dev.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol

log = logging.getLogger(__name__)


class RateLimiter(Protocol):
    async def allow(self, key: str, cost: int = 1) -> bool: ...


class NullRateLimiter:
    """Always allows. Used when rate limiting is disabled or Redis is absent."""

    async def allow(self, key: str, cost: int = 1) -> bool:
        return True


class InMemoryTokenBucket:
    """Process-local token bucket. Not shared across workers — fine for tests/dev."""

    def __init__(
        self,
        capacity: int,
        refill_per_sec: float,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capacity = capacity
        self.refill = refill_per_sec
        self._now = now
        self._state: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)

    async def allow(self, key: str, cost: int = 1) -> bool:
        now = self._now()
        tokens, last = self._state.get(key, (float(self.capacity), now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill)
        allowed = tokens >= cost
        if allowed:
            tokens -= cost
        self._state[key] = (tokens, now)
        return allowed


# Atomic bucket update: read (tokens, ts), refill by elapsed time, spend if enough,
# write back, and expire idle buckets. One round-trip, no races between workers.
_LUA_TOKEN_BUCKET = """
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then tokens = capacity; ts = now end
local delta = math.max(0, now - ts) / 1000.0
tokens = math.min(capacity, tokens + delta * refill)
local allowed = 0
if tokens >= cost then tokens = tokens - cost; allowed = 1 end
redis.call('HMSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity / refill * 1000) + 1000)
return allowed
"""


class RedisTokenBucket:
    def __init__(self, redis_client, capacity: int, refill_per_sec: float) -> None:
        self._redis = redis_client
        self.capacity = capacity
        self.refill = refill_per_sec
        self._sha: str | None = None

    async def ping(self) -> None:
        await self._redis.ping()

    async def allow(self, key: str, cost: int = 1) -> bool:
        if self._sha is None:
            self._sha = await self._redis.script_load(_LUA_TOKEN_BUCKET)
        now_ms = int(time.time() * 1000)
        result = await self._redis.evalsha(
            self._sha, 1, f"rl:{key}", self.capacity, self.refill, now_ms, cost
        )
        return bool(result)


async def build_rate_limiter(settings) -> tuple[RateLimiter, object | None]:
    """Construct the production limiter, falling back to Null if Redis is down.

    Returns (limiter, redis_client_or_None) so the caller can close the client.
    Fail-open here is a deliberate availability choice (documented in README).
    """
    if not settings.rate_limit_enabled:
        return NullRateLimiter(), None
    try:
        import redis.asyncio as redis_async

        client = redis_async.from_url(settings.redis_url)
        await client.ping()
    except Exception as exc:  # noqa: BLE001 - fail open, keep serving
        log.warning("Redis unavailable (%s); rate limiting DISABLED", exc)
        return NullRateLimiter(), None
    return (
        RedisTokenBucket(client, settings.rate_limit_capacity, settings.rate_limit_refill_per_sec),
        client,
    )
