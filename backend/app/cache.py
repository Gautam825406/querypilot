"""Result cache: Redis when REDIS_URL is set, in-memory fallback otherwise.

Cache key = hash(scope | sql | layer_version) — the scope (workspace) and the
semantic-layer version are part of the key, so a definition change or a
different workspace can never serve a stale number. Redis failures degrade
gracefully to the in-memory cache instead of breaking asks.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

from .config import settings

logger = logging.getLogger("querypilot.cache")

_PREFIX = "qp:cache:"


def cache_key(sql: str, layer_version: str, scope: str = "demo") -> str:
    return hashlib.sha256(f"{scope}|{sql}|{layer_version}".encode()).hexdigest()[:16]


class MemoryCache:
    def __init__(self):
        self._data: dict[str, tuple[float, dict]] = {}

    def get(self, key: str):
        entry = self._data.get(key)
        if not entry:
            return None
        ts, value = entry
        if time.time() - ts > settings.cache_ttl_seconds:
            del self._data[key]
            return None
        return value

    def put(self, key: str, value: dict) -> None:
        self._data[key] = (time.time(), value)

    def invalidate(self, scope: str | None = None) -> int:
        if scope is None:
            n = len(self._data)
            self._data.clear()
            return n
        doomed = [k for k in self._data if k.startswith(f"{scope}|")]
        for k in doomed:
            del self._data[k]
        return len(doomed)


class RedisCache:
    """Thin wrapper over redis-py. Any connection error degrades to memory."""

    def __init__(self, url: str):
        import redis
        self._client = redis.Redis.from_url(url, decode_responses=True,
                                            socket_connect_timeout=2, socket_timeout=2)
        self._fallback = MemoryCache()
        self._redis_ok = True

    def _safe(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # Redis down, wrong URL, auth failure, ...
            if self._redis_ok:
                logger.warning("Redis cache unavailable (%s); falling back to memory", e)
                self._redis_ok = False
            return None

    def get(self, key: str):
        raw = self._safe(self._client.get, _PREFIX + key)
        if raw is None:
            return self._fallback.get(key)
        try:
            return json.loads(raw)
        except Exception:
            return None

    def put(self, key: str, value: dict) -> None:
        ok = self._safe(self._client.setex, _PREFIX + key,
                        settings.cache_ttl_seconds, json.dumps(value))
        if ok is None:
            self._fallback.put(key, value)

    def invalidate(self, scope: str | None = None) -> int:
        n = 0
        try:
            for k in self._client.scan_iter(_PREFIX + "*"):
                self._client.delete(k)
                n += 1
        except Exception:
            pass
        return n + self._fallback.invalidate(scope)


_cache = None


def get_cache():
    """Singleton cache backend, chosen from settings at first use."""
    global _cache
    if _cache is None:
        if settings.redis_url:
            try:
                candidate = RedisCache(settings.redis_url)
                candidate._client.ping()
                _cache = candidate
                logger.info("Result cache backend: redis")
            except Exception as e:
                logger.warning("REDIS_URL set but Redis unreachable (%s); using memory cache", e)
                _cache = MemoryCache()
        else:
            _cache = MemoryCache()
    return _cache


def cache_backend_name() -> str:
    """Honest report of the active backend ('redis' only if Redis answered)."""
    backend = get_cache()
    return "redis" if isinstance(backend, RedisCache) and backend._redis_ok else "memory"


def cache_get(key: str):
    return get_cache().get(key)


def cache_put(key: str, value: dict) -> None:
    get_cache().put(key, value)


def cache_invalidate(scope: str | None = None) -> int:
    return get_cache().invalidate(scope)


def reset_cache_singleton() -> None:
    """Tests only: drop the cached backend so a new REDIS_URL is honoured."""
    global _cache
    _cache = None
