"""
GEONI - OpenAI Admin API integration (Costs).

Optional: only active if OPENAI_ADMIN_KEY is configured (a separate Admin
API key from platform.openai.com/settings/organization/admin-keys, distinct
from the regular OPENAI_API_KEY used for scans). OpenAI has no "remaining
balance" endpoint, but the Costs API gives real USD spend - combined with a
manually-entered "total credit loaded" figure (see manual_balances in db.py),
the admin panel can compute an estimated remaining balance itself.

Docs: https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs
"""

import os
import logging
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

OPENAI_ADMIN_KEY = os.environ.get("OPENAI_ADMIN_KEY", "")

# "All-time" is capped to a lookback window rather than account creation
# (unknown): a multi-year range needs dozens of sequential paginated calls,
# and if any single page hits a transient error the whole fetch returns
# early with only the (all-zero) earliest pages summed - reporting $0 spend
# even when real recent spend is nonzero. 120 days is few enough pages to be
# reliable while still covering realistic GEONI-era usage.
ALL_TIME_LOOKBACK_DAYS = 120

# All-time spend can span hundreds of daily buckets (many paginated calls) -
# cache it for a while so every admin panel load doesn't re-walk the full
# history. Past months don't change, so a long TTL is safe.
_all_time_cache = {"value": None, "fetched_at": None}
_all_time_daily_cache = {"value": None, "fetched_at": None}
_ALL_TIME_CACHE_TTL = timedelta(hours=6)

# The month-to-date fetch has no such natural cache - repeated admin panel
# reloads in a short window can hit OpenAI's own rate limit, so cache the
# whole summary briefly too (mirrors anthropic_admin.py).
_summary_cache = {"value": None, "fetched_at": None}
_SUMMARY_CACHE_TTL = timedelta(minutes=5)


async def _fetch_daily_costs(start: datetime, end: datetime) -> dict | None:
    """date (YYYY-MM-DD) -> USD spent that day, from OpenAI's Costs API.
    Returns None (not an empty dict) if the very first page fails - so the
    caller can tell "confirmed zero spend" apart from "we don't actually
    know" and avoid caching a false $0.00."""
    daily = {}
    if not OPENAI_ADMIN_KEY:
        return daily
    got_any_page = False
    try:
        async with httpx.AsyncClient() as client:
            page = None
            for _ in range(60):  # safety cap - all-time range can span many pages of 31 daily buckets
                params = {
                    "start_time": int(start.timestamp()),
                    "end_time": int(end.timestamp()),
                    "bucket_width": "1d",
                    "limit": 31,
                }
                if page:
                    params["page"] = page
                r = await client.get(
                    "https://api.openai.com/v1/organization/costs",
                    headers={"Authorization": f"Bearer {OPENAI_ADMIN_KEY}", "Content-Type": "application/json"},
                    params=params,
                    timeout=20,
                )
                if r.status_code != 200:
                    logger.warning(f"OpenAI costs fetch failed: {r.status_code} {r.text[:200]}")
                    return daily if got_any_page else None
                got_any_page = True
                body = r.json()
                for bucket in body.get("data", []):
                    date_key = datetime.fromtimestamp(bucket["start_time"], tz=timezone.utc).strftime("%Y-%m-%d")
                    for item in bucket.get("results", []):
                        # "value" comes back as a numeric string (e.g. "0.0000123"),
                        # not a float - cast explicitly or this raises on the sum below.
                        amount = float((item.get("amount") or {}).get("value") or 0)
                        daily[date_key] = daily.get(date_key, 0) + amount
                next_page = body.get("next_page")
                if not next_page:
                    break
                page = next_page
    except Exception as e:
        logger.warning(f"OpenAI costs fetch error: {e}")
        return daily if got_any_page else None
    return daily


async def get_openai_cost_summary() -> dict | None:
    """Real USD spend today/last-7-days/month-to-date (for the chart) plus
    all-time spend (for the remaining-balance estimate). Returns None if no
    admin key is configured."""
    if not OPENAI_ADMIN_KEY:
        return None

    now = datetime.now(timezone.utc)

    if _summary_cache["value"] is not None and _summary_cache["fetched_at"] and now - _summary_cache["fetched_at"] < _SUMMARY_CACHE_TTL:
        return _summary_cache["value"]

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month_daily = await _fetch_daily_costs(month_start, now)
    if month_daily is None:
        # Fetch failed outright (e.g. rate limited) - do NOT cache a false
        # $0.00. Serve the last known-good summary if we have one (and don't
        # touch fetched_at, so the next call retries immediately).
        return _summary_cache["value"]

    all_time_is_fresh = True
    if _all_time_cache["value"] is not None and _all_time_cache["fetched_at"] and now - _all_time_cache["fetched_at"] < _ALL_TIME_CACHE_TTL:
        usd_all_time = _all_time_cache["value"]
    else:
        all_time_daily = await _fetch_daily_costs(now - timedelta(days=ALL_TIME_LOOKBACK_DAYS), now)
        if all_time_daily is None:
            # Fetch failed - fall back to the last known value if we have one,
            # but if we don't (e.g. right after a restart), showing 0 here is
            # a stopgap that must NOT get locked into the summary cache below,
            # or a transient failure becomes a persistent wrong $0.00.
            usd_all_time = _all_time_cache["value"] or 0.0
            all_time_is_fresh = _all_time_cache["value"] is not None
        else:
            usd_all_time = sum(all_time_daily.values())
            _all_time_cache["value"] = usd_all_time
            _all_time_cache["fetched_at"] = now
            _all_time_daily_cache["value"] = all_time_daily
            _all_time_daily_cache["fetched_at"] = now

    today_key = now.strftime("%Y-%m-%d")
    week_start_key = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    usd_today = month_daily.get(today_key, 0)
    usd_week = sum(v for k, v in month_daily.items() if k >= week_start_key)
    usd_month = sum(month_daily.values())

    result = {
        "usd_today": round(usd_today, 4),
        "usd_week": round(usd_week, 4),
        "usd_month": round(usd_month, 4),
        "usd_all_time": round(usd_all_time, 4),
        "daily": [{"date": d, "usd": round(v, 4)} for d, v in sorted(month_daily.items())],
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if all_time_is_fresh:
        _summary_cache["value"] = result
        _summary_cache["fetched_at"] = now
    return result


async def get_openai_monthly_breakdown() -> dict[str, float] | None:
    """USD cost grouped by calendar month (YYYY-MM), reusing the same
    ALL_TIME_LOOKBACK_DAYS cached daily data as usd_all_time."""
    if not OPENAI_ADMIN_KEY:
        return None
    now = datetime.now(timezone.utc)
    if _all_time_daily_cache["value"] is not None and _all_time_daily_cache["fetched_at"] and now - _all_time_daily_cache["fetched_at"] < _ALL_TIME_CACHE_TTL:
        daily = _all_time_daily_cache["value"]
    else:
        daily = await _fetch_daily_costs(now - timedelta(days=ALL_TIME_LOOKBACK_DAYS), now)
        if daily is None:
            return None
        _all_time_daily_cache["value"] = daily
        _all_time_daily_cache["fetched_at"] = now
        _all_time_cache["value"] = sum(daily.values())
        _all_time_cache["fetched_at"] = now
    monthly = {}
    for date_key, amount in daily.items():
        month_key = date_key[:7]
        monthly[month_key] = monthly.get(month_key, 0) + amount
    return {k: round(v, 4) for k, v in monthly.items()}
