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
import logging
from datetime import datetime, timedelta, timezone
import httpx

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
ALL_TIME_LOOKBACK_DAYS = 120
_all_time_cache = {"value": None, "fetched_at": None}
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
                r = await client.get(
                    "https://api.anthropic.com/v1/organizations/cost_report",
                    headers={"anthropic-version": ANTHROPIC_VERSION, "x-api-key": ANTHROPIC_ADMIN_KEY},
                    params=params,
                    timeout=15,
                )
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
    except Exception as e:
        logger.warning(f"Anthropic cost report error: {e}")
        return daily_cents if got_any_page else None
    return daily_cents


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

    if _all_time_cache["value"] is not None and _all_time_cache["fetched_at"] and now - _all_time_cache["fetched_at"] < _ALL_TIME_CACHE_TTL:
        usd_all_time = _all_time_cache["value"]
    else:
        all_time_cents = await _fetch_daily_cents(now - timedelta(days=ALL_TIME_LOOKBACK_DAYS), now)
        if all_time_cents is None:
            usd_all_time = _all_time_cache["value"] or 0.0
        else:
            usd_all_time = sum(all_time_cents.values()) / 100
            _all_time_cache["value"] = usd_all_time
            _all_time_cache["fetched_at"] = now

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
    }
    _summary_cache["value"] = result
    _summary_cache["fetched_at"] = now
    return result
