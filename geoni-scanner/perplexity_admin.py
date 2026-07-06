"""
GEONI - Perplexity self-computed cost tracking.

Perplexity has no cost/usage API at all (confirmed: the "Computer Analytics
API" exists but tracks a completely different product - Comet's routing to
third-party models like Claude/GPT, not the Sonar search API GEONI actually
calls). So instead of fetching real spend, GEONI computes it itself from the
token counts already returned in every Perplexity response, using their
published pricing - then persists it (db.log_perplexity_usage) so history
survives restarts, exactly like a real cost API would provide.

Pricing (docs.perplexity.ai/docs/getting-started/pricing, "sonar" model -
the only model GEONI calls, see brand_recall.py):
- $1 / 1M input tokens, $1 / 1M output tokens
- Per-request search fee based on search_context_size; GEONI never sets
  web_search_options, so every call defaults to "low" = $5 / 1,000 requests
"""

from datetime import datetime, timedelta, timezone

from db import log_perplexity_usage, get_perplexity_cost_daily

INPUT_PRICE_PER_M = 1.0
OUTPUT_PRICE_PER_M = 1.0
LOW_CONTEXT_REQUEST_FEE = 0.005  # $5 / 1000 requests, default "low" search_context_size

ALL_TIME_LOOKBACK_DAYS = 120


def compute_cost(prompt_tokens: int, completion_tokens: int) -> float:
    token_cost = (prompt_tokens / 1_000_000) * INPUT_PRICE_PER_M + (completion_tokens / 1_000_000) * OUTPUT_PRICE_PER_M
    return token_cost + LOW_CONTEXT_REQUEST_FEE


async def record_perplexity_call(usage: dict) -> None:
    """Fire-and-forget: call this right after a successful Perplexity response
    with its `usage` object, mirroring log_provider_call's call-count tracking
    but also persisting the computed USD cost."""
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    cost = compute_cost(prompt_tokens, completion_tokens)
    await log_perplexity_usage(cost, prompt_tokens, completion_tokens)


async def get_perplexity_cost_summary() -> dict:
    """Same shape as openai_admin/anthropic_admin's summaries (usd_today/
    usd_week/usd_month/usd_all_time/daily) so the admin panel can reuse the
    same stat-tile + TopupSection layout."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month_daily = await get_perplexity_cost_daily(month_start, now)
    all_time_daily = await get_perplexity_cost_daily(now - timedelta(days=ALL_TIME_LOOKBACK_DAYS), now)
    usd_all_time = sum(all_time_daily.values())

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
        "estimated": True,
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
