"""
GEONI Scanner - Rate Limiting
Multi-dimensional rate limiting to prevent abuse: limits requests per IP,
per email address, and per domain independently. Uses an in-memory sliding
window for MVP; the store interface is designed to be swapped for Redis
later without changing call sites (see RATE_LIMIT_STORE).

Limits (tunable via environment variables):
- Per IP:     5 requests / 10 minutes  (catches scripted abuse from one source)
- Per email:  2 requests / 60 minutes  (catches one person spamming audits)
- Per domain: 2 requests / 60 minutes  (catches repeated scans of the same target,
                                         which is wasted Anthropic/crawl spend)
"""

import os
import time
import logging
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

IP_LIMIT = int(os.environ.get("RATE_LIMIT_IP_COUNT", "5"))
IP_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_IP_WINDOW", "600"))  # 10 min

EMAIL_LIMIT = int(os.environ.get("RATE_LIMIT_EMAIL_COUNT", "5"))
EMAIL_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_EMAIL_WINDOW", "3600"))  # 60 min

DOMAIN_LIMIT = int(os.environ.get("RATE_LIMIT_DOMAIN_COUNT", "5"))
DOMAIN_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_DOMAIN_WINDOW", "3600"))  # 60 min


@dataclass
class _Bucket:
    timestamps: list = field(default_factory=list)


class RateLimitExceeded(Exception):
    def __init__(self, dimension: str, retry_after_seconds: int):
        self.dimension = dimension
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Rate limit exceeded for {dimension}, retry after {retry_after_seconds}s")


class InMemoryRateLimiter:
    """
    Sliding-window rate limiter. Not distributed (per-process state) —
    fine for a single ECS task. If/when scaled to multiple tasks, swap
    this class for a Redis-backed implementation with the same interface
    (check_and_record) without touching call sites in main.py.
    """

    def __init__(self):
        self._buckets: dict[str, _Bucket] = defaultdict(_Bucket)

    def _prune(self, bucket: _Bucket, window_seconds: int, now: float):
        cutoff = now - window_seconds
        bucket.timestamps = [t for t in bucket.timestamps if t > cutoff]

    def check_and_record(self, key: str, limit: int, window_seconds: int) -> None:
        """
        Raises RateLimitExceeded if the key has hit its limit within the window.
        Otherwise records this request and allows it through.
        """
        now = time.monotonic()
        bucket = self._buckets[key]
        self._prune(bucket, window_seconds, now)

        if len(bucket.timestamps) >= limit:
            oldest = bucket.timestamps[0]
            retry_after = int(window_seconds - (now - oldest)) + 1
            raise RateLimitExceeded(dimension=key, retry_after_seconds=max(retry_after, 1))

        bucket.timestamps.append(now)


# In-memory (tek proses) — Redis yoksa/erisilemezse fallback.
RATE_LIMIT_STORE = InMemoryRateLimiter()


class _RedisRateLimiter:
    """Dagitik SABIT-pencere sayaci (Upstash/Redis). Coklu App Runner instance'inda
    PAYLASIMLI -> rate-limit instance sayisindan bagimsiz dogru kalir (autoscale'de
    in-memory'nin instance-sayisinca gevsemesi cozulur). INCR+EXPIRE tek pipeline
    (atomik, ucuz). Kisa timeout: rate-limit kontrolu event loop'u uzun bloklamasin;
    Redis yavas/olu ise hizli fail -> cagiran in-memory'ye duser."""

    def __init__(self, url: str):
        import redis as _redis  # requirements'ta rezerve; yalnizca REDIS_URL varsa yuklenir
        self._r = _redis.from_url(
            url, socket_timeout=0.3, socket_connect_timeout=0.3, decode_responses=True,
        )
        self._r.ping()  # baglanti dogrulama (basarisizsa kurulum iptal -> in-memory)

    def check_and_record(self, key: str, limit: int, window_seconds: int) -> None:
        now = int(time.time())
        rkey = f"rl:{key}:{now // window_seconds}"  # sabit-pencere kova
        pipe = self._r.pipeline()
        pipe.incr(rkey)
        pipe.expire(rkey, window_seconds)
        count = pipe.execute()[0]
        if count > limit:
            retry_after = window_seconds - (now % window_seconds)
            raise RateLimitExceeded(dimension=key, retry_after_seconds=max(retry_after, 1))


# REDIS_URL gercek bir Upstash/Redis ise paylasimli limiter'i kur (localhost
# varsayilani = yapilandirilmamis -> in-memory). Baglanamazsa sessizce in-memory.
_REDIS_URL = os.environ.get("REDIS_URL", "")
_redis_limiter = None
if _REDIS_URL and not _REDIS_URL.startswith("redis://localhost"):
    try:
        _redis_limiter = _RedisRateLimiter(_REDIS_URL)
        logger.info("Rate limit: paylasimli Redis aktif (Upstash)")
    except Exception as e:
        logger.warning(f"Rate limit: Redis baglanamadi, in-memory fallback: {e}")
        _redis_limiter = None


def _check(key: str, limit: int, window_seconds: int) -> None:
    """Redis varsa paylasimli sayac; RateLimitExceeded disi HERHANGI bir Redis
    hatasinda in-memory'ye duser (rate-limit asla uygulamayi kirmaz — fail-open)."""
    if _redis_limiter is not None:
        try:
            _redis_limiter.check_and_record(key, limit, window_seconds)
            return
        except RateLimitExceeded:
            raise
        except Exception as e:
            logger.warning(f"Rate limit Redis hatasi, in-memory'ye dusuluyor: {e}")
    RATE_LIMIT_STORE.check_and_record(key, limit, window_seconds)


def enforce_audit_rate_limits(client_ip: str, email: str, domain: str, skip_ip: bool = False) -> None:
    """
    Enforce all three rate limit dimensions for an incoming audit request.
    Call this before enqueueing the background job. Raises RateLimitExceeded
    on the first dimension that's violated (IP checked first since it's
    cheapest to hit and most indicative of scripted abuse). Redis (paylasimli)
    varsa onu, yoksa in-memory'yi kullanir — cagiran imzasi degismez.
    """
    normalized_email = email.strip().lower()
    normalized_domain = domain.strip().lower()

    try:
        # skip_ip: mobil (native) istemciler icin per-IP atlanir. Sebep: carrier-grade
        # NAT ( or. Turkcell) BINLERCE mobil kullaniciyi TEK IP'de toplar -> per-IP 5/10dk
        # gercek kullanicilari toplu bloklar. Mobilde asil anti-abuse DeviceCheck (cihaz-basi)
        # + kredi + ASAGIDAKI email/domain backstop'u; per-IP zararli, cikarilir. Web'de KALIR.
        if not skip_ip:
            _check(f"ip:{client_ip}", IP_LIMIT, IP_WINDOW_SECONDS)
        _check(f"email:{normalized_email}", EMAIL_LIMIT, EMAIL_WINDOW_SECONDS)
        _check(f"domain:{normalized_domain}", DOMAIN_LIMIT, DOMAIN_WINDOW_SECONDS)
    except RateLimitExceeded as e:
        logger.warning(f"Rate limit hit: {e.dimension}, retry after {e.retry_after_seconds}s")
        raise



# Promosyon kodu deneme siniri — KABA KUVVET korumasi.
# Kod uzayi 31^10 (~8x10^14) yani tahmin pratikte imkansiz; yine de sinirsiz
# deneme (a) sunucuya bedava yuk bindirir, (b) sizmis bir parti listesinin
# hangi kodlarinin hala gecerli oldugunu taratmayi ucuzlatir. Hesap BASINA
# sinirlanir (uc auth zorunlu), IP'ye degil: mobilde carrier NAT bininlerce
# kullaniciyi tek IP'de topluyor (bkz. enforce_audit_rate_limits notu).
PROMO_DENEME_LIMITI = int(os.environ.get("PROMO_ATTEMPT_LIMIT", "10"))
PROMO_DENEME_PENCERESI = int(os.environ.get("PROMO_ATTEMPT_WINDOW", "600"))  # 10 dk


def enforce_promo_rate_limit(user_id: str) -> None:
    """RateLimitExceeded firlatir. Basarili kullanimda da sayar — zaten hesap
    basina tek kod kullanilabiliyor, mesru kullanici limite yaklasmaz."""
    _check(f"promo:{user_id}", PROMO_DENEME_LIMITI, PROMO_DENEME_PENCERESI)
