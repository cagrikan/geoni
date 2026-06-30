"""
GEONI Scanner - Rate Limiting
Multi-dimensional rate limiting to prevent abuse: limits requests per IP,
per email address, and per domain independently. Uses an in-memory sliding
window for MVP; the store interface is designed to be swapped for Redis
later without changing call sites (see RATE_LIMIT_STORE).

Limits (tunable via environment variables):
- Per IP:     5 requests / 10 minutes  (catches scripted abuse from one source)
- Per email:  3 requests / 60 minutes  (catches one person spamming audits)
- Per domain: 3 requests / 60 minutes  (catches repeated scans of the same target,
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

EMAIL_LIMIT = int(os.environ.get("RATE_LIMIT_EMAIL_COUNT", "3"))
EMAIL_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_EMAIL_WINDOW", "3600"))  # 60 min

DOMAIN_LIMIT = int(os.environ.get("RATE_LIMIT_DOMAIN_COUNT", "3"))
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


# Single shared instance for the process (mirrors the in-memory jobs_store pattern in main.py)
RATE_LIMIT_STORE = InMemoryRateLimiter()


def enforce_audit_rate_limits(client_ip: str, email: str, domain: str) -> None:
    """
    Enforce all three rate limit dimensions for an incoming audit request.
    Call this before enqueueing the background job. Raises RateLimitExceeded
    on the first dimension that's violated (IP checked first since it's
    cheapest to hit and most indicative of scripted abuse).
    """
    normalized_email = email.strip().lower()
    normalized_domain = domain.strip().lower()

    try:
        RATE_LIMIT_STORE.check_and_record(f"ip:{client_ip}", IP_LIMIT, IP_WINDOW_SECONDS)
        RATE_LIMIT_STORE.check_and_record(f"email:{normalized_email}", EMAIL_LIMIT, EMAIL_WINDOW_SECONDS)
        RATE_LIMIT_STORE.check_and_record(f"domain:{normalized_domain}", DOMAIN_LIMIT, DOMAIN_WINDOW_SECONDS)
    except RateLimitExceeded as e:
        logger.warning(f"Rate limit hit: {e.dimension}, retry after {e.retry_after_seconds}s")
        raise

