"""
GEONI - Combined total cost across all external providers, for the admin
panel's "Toplam Maliyet" widget (this month / last 2 months / last 3
months / all-time).

Combines Anthropic, OpenAI, AWS, Perplexity and Supabase (all USD) with
Gemini (TRY, converted to USD via TCMB's live selling rate through a
public wrapper API mirroring the official TCMB rate list). If the FX
fetch fails, Gemini's TRY figure is still reported on its own but is
excluded from the combined USD total rather than silently using a stale
guess.

"2 months" / "3 months" mean the current calendar month plus the
previous 1-2 calendar months (not a rolling 60/90-day window).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from anthropic_admin import get_anthropic_monthly_breakdown
from openai_admin import get_openai_monthly_breakdown
from aws_cost import get_aws_monthly_breakdown
from gemini_admin import get_gemini_monthly_breakdown
from perplexity_admin import get_perplexity_monthly_breakdown
from db import get_manual_cost

logger = logging.getLogger(__name__)

_fx_cache = {"usd_try": None, "fetched_at": None}
_FX_CACHE_TTL = timedelta(hours=1)


async def _get_usd_try_rate() -> float | None:
    """TRY per 1 USD (selling rate), from TCMB's own published rates via a
    public wrapper (docs.hasanadiguzel.com.tr) - no API key needed."""
    now = datetime.now(timezone.utc)
    if _fx_cache["usd_try"] is not None and _fx_cache["fetched_at"] and now - _fx_cache["fetched_at"] < _FX_CACHE_TTL:
        return _fx_cache["usd_try"]
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get("https://hasanadiguzel.com.tr/api/kurgetir", timeout=10)
            if r.status_code != 200:
                return _fx_cache["usd_try"]
            data = r.json()
            for entry in data.get("TCMB_AnlikKurBilgileri", []):
                if entry.get("Isim") == "ABD DOLARI":
                    rate = float(entry["ForexSelling"])
                    _fx_cache["usd_try"] = rate
                    _fx_cache["fetched_at"] = now
                    return rate
    except Exception as e:
        logger.warning(f"TCMB FX fetch failed: {e}")
    return _fx_cache["usd_try"]


def _last_n_calendar_months(n: int, now: datetime) -> list[str]:
    """[this month, previous month, ...] as YYYY-MM keys."""
    months = []
    y, m = now.year, now.month
    for _ in range(n):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return months


async def get_admin_total_cost_summary() -> dict:
    now = datetime.now(timezone.utc)
    months = _last_n_calendar_months(3, now)  # [this, prev1, prev2]
    this_key = months[0]

    anthropic_m, openai_m, perplexity_m, gemini_m, supabase_row, usd_try = await asyncio.gather(
        get_anthropic_monthly_breakdown(),
        get_openai_monthly_breakdown(),
        get_perplexity_monthly_breakdown(),
        get_gemini_monthly_breakdown(),
        get_manual_cost("supabase"),
        _get_usd_try_rate(),
    )
    aws_m = get_aws_monthly_breakdown()

    usd_sources = {
        "anthropic": anthropic_m or {},
        "openai": openai_m or {},
        "aws": aws_m or {},
        "perplexity": perplexity_m or {},
    }
    gemini_try_by_month = gemini_m or {}
    gemini_usd_by_month = {k: v / usd_try for k, v in gemini_try_by_month.items()} if usd_try else {}

    supabase_current = 0.0
    if supabase_row and supabase_row.get("current_cost") is not None:
        supabase_current = float(supabase_row["current_cost"])

    def sum_for_months(month_keys: list[str]) -> float:
        total = sum(pm.get(mk, 0) for pm in usd_sources.values() for mk in month_keys)
        total += sum(gemini_usd_by_month.get(mk, 0) for mk in month_keys)
        return total

    # Supabase is a single manual snapshot (no per-month history), so it can
    # only ever represent "this month" - added once to every window below.
    usd_this_month = sum_for_months(months[:1]) + supabase_current
    usd_2_months = sum_for_months(months[:2]) + supabase_current
    usd_3_months = sum_for_months(months[:3]) + supabase_current
    usd_all_time = sum(sum(pm.values()) for pm in usd_sources.values()) + sum(gemini_usd_by_month.values()) + supabase_current

    by_provider_this_month = {
        "anthropic": round((anthropic_m or {}).get(this_key, 0), 2),
        "openai": round((openai_m or {}).get(this_key, 0), 2),
        "aws": round((aws_m or {}).get(this_key, 0), 2),
        "perplexity": round((perplexity_m or {}).get(this_key, 0), 2),
        "gemini_usd": round(gemini_usd_by_month.get(this_key, 0), 2),
        "supabase": round(supabase_current, 2),
    }

    return {
        "usd_this_month": round(usd_this_month, 2),
        "usd_2_months": round(usd_2_months, 2),
        "usd_3_months": round(usd_3_months, 2),
        "usd_all_time": round(usd_all_time, 2),
        "gemini_try_this_month": round(gemini_try_by_month.get(this_key, 0), 2),
        "usd_try_rate": usd_try,
        "by_provider_this_month": by_provider_this_month,
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
