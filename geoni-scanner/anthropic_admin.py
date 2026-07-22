"""
GEONI - Anthropic Admin API integration (Usage & Cost).

Optional: only active if ANTHROPIC_ADMIN_KEY is configured (a separate
Admin API key from Claude Console, distinct from the regular ANTHROPIC_API_KEY
used for scans). Provides real USD cost data for the admin panel - the only
one of the four external AI motors (OpenAI, Anthropic, Google, Perplexity)
that exposes a real cost/usage endpoint via API key at all.

Docs: https://platform.claude.com/docs/en/build-with-claude/usage-cost-api
"""

import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import httpx


async def _cost_get_with_retry(client: httpx.AsyncClient, params: dict, attempts: int = 4):
    """Anthropic cost_report tek sayfa; 429/5xx'te ustel backoff (+ Retry-After) ile
    yeniden dener. Kok-neden: sayfalama 60 hizli ardisik istek yapip 429 yiyordu."""
    delay = 0.5
    r = None
    for i in range(attempts):
        r = await client.get(
            "https://api.anthropic.com/v1/organizations/cost_report",
            headers={"anthropic-version": ANTHROPIC_VERSION, "x-api-key": ANTHROPIC_ADMIN_KEY},
            params=params, timeout=15,
        )
        if r.status_code == 200 or i == attempts - 1:
            return r
        if r.status_code in (429, 500, 502, 503, 529):
            ra = r.headers.get("retry-after", "")
            wait = float(ra) if ra.replace(".", "", 1).isdigit() else delay
            await asyncio.sleep(min(wait, 8))
            delay *= 2
            continue
        return r  # yeniden-denenemez hata
    return r

logger = logging.getLogger(__name__)

ANTHROPIC_ADMIN_KEY = os.environ.get("ANTHROPIC_ADMIN_KEY", "")
ANTHROPIC_VERSION = "2023-06-01"
FMT = "%Y-%m-%dT%H:%M:%SZ"

# "All-time" is capped to a lookback window rather than a fixed old epoch: a
# multi-year range needs many sequential paginated calls, and if any single
# page errors the whole fetch returns early with only the (all-zero) earliest
# pages summed - reporting $0 spend even when real recent spend is nonzero.
# 120 days is few enough pages to be reliable while still covering realistic
# GEONI-era usage. Cached so every admin panel load doesn't re-walk it.
ALL_TIME_LOOKBACK_DAYS = 365  # B2 (2026-07-22): 120->365, dusuk-bakiye false-negative penceresi daraltildi (hesaplar <1yil -> etkin tum-zaman)
_all_time_daily_cache = {"value": None, "fetched_at": None}
_ALL_TIME_CACHE_TTL = timedelta(hours=6)

# The month-to-date fetch has no such natural cache, so repeated admin panel
# reloads within a short window (or several admins/tabs open at once) hit
# Anthropic's own rate limit (429) - cache the whole summary briefly too.
_summary_cache = {"value": None, "fetched_at": None}
_SUMMARY_CACHE_TTL = timedelta(minutes=5)


async def _fetch_daily_cents(start: datetime, end: datetime) -> dict | None:
    """date (YYYY-MM-DD) -> cost in cents, from Anthropic's Cost Report API.
    Returns None (not an empty dict) if the very first page fails - e.g. a
    429 - so the caller can tell "confirmed zero spend" apart from "we
    don't actually know" and avoid caching a false $0.00."""
    daily_cents = {}
    if not ANTHROPIC_ADMIN_KEY:
        return daily_cents
    got_any_page = False
    try:
        async with httpx.AsyncClient() as client:
            page = None
            for _ in range(60):  # safety cap - all-time range can span many pages
                params = {"starting_at": start.strftime(FMT), "ending_at": end.strftime(FMT)}
                if page:
                    params["page"] = page
                r = await _cost_get_with_retry(client, params)
                if r.status_code != 200:
                    logger.warning(f"Anthropic cost report failed: {r.status_code} {r.text[:200]}")
                    return daily_cents if got_any_page else None
                got_any_page = True
                body = r.json()
                for bucket in body.get("data", []):
                    date_key = bucket.get("starting_at", "")[:10]
                    if not date_key:
                        continue
                    for item in bucket.get("results", []):
                        amount = float(item.get("amount") or 0)
                        daily_cents[date_key] = daily_cents.get(date_key, 0) + amount
                if not body.get("has_more"):
                    break
                page = body.get("next_page")
                if not page:
                    break
                await asyncio.sleep(0.15)  # sayfalar arasi nazik gecikme (429 onlemi)
    except Exception as e:
        logger.warning(f"Anthropic cost report error: {e}")
        return daily_cents if got_any_page else None
    return daily_cents


async def _get_all_time_daily(now: datetime, fresh_month_cents: dict) -> tuple[dict, bool]:
    """Returns (daily_cents dict covering ALL_TIME_LOOKBACK_DAYS, is_fresh).

    The 120-day fetch is cached for hours (cheap on Anthropic's rate limit),
    but that means it can lag behind the always-fresh month-to-date fetch by
    up to _ALL_TIME_CACHE_TTL. Rather than just clamping the resulting sum
    (which would silently understate real historical spend), we overlay the
    fresh month_cents on top of the cached dict before returning it, so the
    days that changed since the cache was built are correct and only the
    genuinely-unchanged older days are served from cache."""
    is_fresh = True
    if _all_time_daily_cache["value"] is not None and _all_time_daily_cache["fetched_at"] and now - _all_time_daily_cache["fetched_at"] < _ALL_TIME_CACHE_TTL:
        daily = dict(_all_time_daily_cache["value"])
    else:
        fetched = await _fetch_daily_cents(now - timedelta(days=ALL_TIME_LOOKBACK_DAYS), now)
        if fetched is None:
            daily = dict(_all_time_daily_cache["value"] or {})
            is_fresh = _all_time_daily_cache["value"] is not None
        else:
            daily = fetched
            _all_time_daily_cache["value"] = fetched
            _all_time_daily_cache["fetched_at"] = now
    daily.update(fresh_month_cents)  # always-fresh data wins over the cached snapshot
    return daily, is_fresh


async def get_anthropic_cost_summary() -> dict | None:
    """Real USD cost today/last-7-days/month-to-date (for the chart) plus
    all-time spend (for the remaining-balance estimate).
    Returns None if no admin key is configured (feature simply not enabled)."""
    if not ANTHROPIC_ADMIN_KEY:
        return None

    now = datetime.now(timezone.utc)

    if _summary_cache["value"] is not None and _summary_cache["fetched_at"] and now - _summary_cache["fetched_at"] < _SUMMARY_CACHE_TTL:
        return _summary_cache["value"]

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month_cents = await _fetch_daily_cents(month_start, now)
    if month_cents is None:
        # Fetch failed outright (e.g. rate limited) - do NOT cache a false
        # $0.00. Serve the last known-good summary if we have one (and don't
        # touch fetched_at, so the next call retries immediately rather than
        # waiting out the full TTL).
        return _summary_cache["value"]

    all_time_daily, all_time_is_fresh = await _get_all_time_daily(now, month_cents)
    usd_all_time = sum(all_time_daily.values()) / 100

    today_key = now.strftime("%Y-%m-%d")
    week_start_key = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    usd_today = month_cents.get(today_key, 0) / 100
    usd_week = sum(c for d, c in month_cents.items() if d >= week_start_key) / 100
    usd_month = sum(month_cents.values()) / 100

    result = {
        "usd_today": round(usd_today, 4),
        "usd_week": round(usd_week, 4),
        "usd_month": round(usd_month, 4),
        "usd_all_time": round(usd_all_time, 4),
        "daily": [{"date": d, "usd": round(c / 100, 4)} for d, c in sorted(month_cents.items())],
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if all_time_is_fresh:
        _summary_cache["value"] = result
        _summary_cache["fetched_at"] = now
    return result


async def get_anthropic_monthly_breakdown() -> dict[str, float] | None:
    """USD cost grouped by calendar month (YYYY-MM), covering the same
    ALL_TIME_LOOKBACK_DAYS window already fetched for usd_all_time - reuses
    that cached daily data (with the current month kept fresh) instead of
    an extra full paginated API call."""
    if not ANTHROPIC_ADMIN_KEY:
        return None
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_cents = await _fetch_daily_cents(month_start, now)
    if month_cents is None:
        month_cents = {}
    daily_cents, is_fresh = await _get_all_time_daily(now, month_cents)
    if not is_fresh and not daily_cents:
        return None
    monthly = {}
    for date_key, cents in daily_cents.items():
        month_key = date_key[:7]
        monthly[month_key] = monthly.get(month_key, 0) + cents / 100
    return {k: round(v, 4) for k, v in monthly.items()}
