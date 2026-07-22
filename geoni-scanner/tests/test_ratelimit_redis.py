"""Rate-limit Redis fail-safe kilidi (C1, derin test 2026-07-22).

Redis paylasimli limiter eklendi (autoscale'de in-memory'nin gevsemesi cozulur).
KRITIK: Redis HERHANGI bir hatada in-memory'ye dusmeli (rate-limit asla
uygulamayi kirmaz). Bu testler o fail-open davranisini + limit propagation'i
sabitler. (Gercek Redis'e baglanmaz; stub'larla.)
"""
import ratelimit
from ratelimit import enforce_audit_rate_limits, RateLimitExceeded, InMemoryRateLimiter


def _reset(monkeypatch):
    monkeypatch.setattr(ratelimit, "RATE_LIMIT_STORE", InMemoryRateLimiter())
    monkeypatch.setattr(ratelimit, "_redis_limiter", None)


def test_inmemory_enforces_ip_limit(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(ratelimit, "IP_LIMIT", 2)
    enforce_audit_rate_limits("1.1.1.1", "a@x.com", "a.com")
    enforce_audit_rate_limits("1.1.1.1", "b@x.com", "b.com")
    try:
        enforce_audit_rate_limits("1.1.1.1", "c@x.com", "c.com")
        assert False, "3. istekte IP limiti patlamaliydi"
    except RateLimitExceeded as e:
        assert e.dimension.startswith("ip:")


def test_redis_error_falls_back_to_inmemory(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(ratelimit, "IP_LIMIT", 1)

    class BoomRedis:
        def check_and_record(self, *a):
            raise ConnectionError("redis down")

    monkeypatch.setattr(ratelimit, "_redis_limiter", BoomRedis())
    # Redis patlasa da in-memory devrede: 1. gecer, 2. IP patlar
    enforce_audit_rate_limits("2.2.2.2", "a@x.com", "a.com")
    try:
        enforce_audit_rate_limits("2.2.2.2", "b@x.com", "b.com")
        assert False, "Redis fallback in-memory limiti uygulamaliydi"
    except RateLimitExceeded:
        pass


def test_redis_limit_propagates(monkeypatch):
    _reset(monkeypatch)

    class BlockRedis:
        def check_and_record(self, key, limit, w):
            if key.startswith("ip:"):
                raise RateLimitExceeded(dimension=key, retry_after_seconds=42)

    monkeypatch.setattr(ratelimit, "_redis_limiter", BlockRedis())
    try:
        enforce_audit_rate_limits("3.3.3.3", "a@x.com", "a.com")
        assert False, "Redis'in RateLimitExceeded'i propagate olmaliydi"
    except RateLimitExceeded as e:
        assert e.retry_after_seconds == 42
