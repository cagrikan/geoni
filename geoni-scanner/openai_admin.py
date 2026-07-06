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

# All-time spend is summed from this fixed epoch rather than account creation
# (unknown) - safely early enough to cover any GEONI-era OpenAI usage.
ALL_TIME_START = datetime(2024, 1, 1, tzinfo=timezone.utc)

# All-time spend can span hundreds of daily buckets (many paginated calls) -
# cache it for a while so every admin panel load doesn't re-walk the full
# history. Past months don't change, so a long TTL is safe.
_all_time_cache = {"value": None, "fetched_at": None}
_ALL_TIME_CACHE_TTL = timedelta(hours=6)


async def _fetch_daily_costs(start: datetime, end: datetime) -> dict:
    """date (YYYY-MM-DD) -> USD spent that day, from OpenAI's Costs API."""
    daily = {}
    if not OPENAI_ADMIN_KEY:
        return daily
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
                    return daily
                body = r.json()
                for bucket in body.get("data", []):
                    date_key = datetime.fromtimestamp(bucket["start_time"], tz=timezone.utc).strftime("%Y-%m-%d")
                    for item in bucket.get("results", []):
                        amount = (item.get("amount") or {}).get("value") or 0
                        daily[date_key] = daily.get(date_key, 0) + amount
                next_page = body.get("next_page")
                if not next_page:
                    break
                page = next_page
    except Exception as e:
        logger.warning(f"OpenAI costs fetch error: {e}")
    return daily


async def get_openai_cost_summary() -> dict | None:
    """Real USD spend today/last-7-days/month-to-date (for the chart) plus
    all-time spend (for the remaining-balance estimate). Returns None if no
    admin key is configured."""
    if not OPENAI_ADMIN_KEY:
        return None

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month_daily = await _fetch_daily_costs(month_start, now)

    if _all_time_cache["value"] is not None and _all_time_cache["fetched_at"] and now - _all_time_cache["fetched_at"] < _ALL_TIME_CACHE_TTL:
        usd_all_time = _all_time_cache["value"]
    else:
        all_time_daily = await _fetch_daily_costs(ALL_TIME_START, now)
        usd_all_time = sum(all_time_daily.values())
        _all_time_cache["value"] = usd_all_time
        _all_time_cache["fetched_at"] = now

    today_key = now.strftime("%Y-%m-%d")
    week_start_key = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    usd_today = month_daily.get(today_key, 0)
    usd_week = sum(v for k, v in month_daily.items() if k >= week_start_key)
    usd_month = sum(month_daily.values())

    return {
        "usd_today": round(usd_today, 4),
        "usd_week": round(usd_week, 4),
        "usd_month": round(usd_month, 4),
        "usd_all_time": round(usd_all_time, 4),
        "daily": [{"date": d, "usd": round(v, 4)} for d, v in sorted(month_daily.items())],
    }
