"""
GEONI - AWS Cost Explorer integration.

Real infrastructure cost (ECS, ALB, ECR, etc.) for the admin panel, next to
the Anthropic API cost. Requires the ECS task role (ecsTaskRole) to have
ce:GetCostAndUsage - see admin panel setup notes.

Cost Explorer bills $0.01 per API call, so results are cached in-process for
a while rather than refetched on every admin panel load.
"""

import logging
from datetime import datetime, timedelta, timezone

import boto3

logger = logging.getLogger(__name__)

_cache = {"data": None, "fetched_at": None}
CACHE_TTL = timedelta(minutes=30)


def get_aws_cost_summary() -> dict | None:
    """Daily USD cost today/last-7-days/month-to-date, plus a top-6 service
    breakdown, fetched once from the start of the current month. Cost
    Explorer is only available in us-east-1 regardless of where your
    resources actually run. Returns None if the call fails (e.g. missing
    IAM permission) so the panel can fall back gracefully."""
    now = datetime.now(timezone.utc)
    if _cache["data"] is not None and _cache["fetched_at"] and now - _cache["fetched_at"] < CACHE_TTL:
        return _cache["data"]

    start = now.replace(day=1).strftime("%Y-%m-%d")
    end = (now + timedelta(days=1)).strftime("%Y-%m-%d")  # End is exclusive

    try:
        client = boto3.client("ce", region_name="us-east-1")
        resp = client.get_cost_and_usage(
            TimePeriod={"Start": start, "End": end},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
    except Exception as e:
        logger.warning(f"AWS Cost Explorer fetch failed: {e}")
        return None

    daily = []
    by_service = {}
    today_key = now.strftime("%Y-%m-%d")
    week_start_key = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    usd_today = 0.0
    usd_week = 0.0
    usd_month = 0.0

    for day in resp.get("ResultsByTime", []):
        date_key = day["TimePeriod"]["Start"]
        day_total = 0.0
        for group in day.get("Groups", []):
            service = group["Keys"][0]
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
            day_total += amount
            by_service[service] = by_service.get(service, 0) + amount
        daily.append({"date": date_key, "usd": round(day_total, 4)})
        usd_month += day_total
        if date_key >= week_start_key:
            usd_week += day_total
        if date_key == today_key:
            usd_today = day_total

    top_services = dict(sorted(by_service.items(), key=lambda kv: -kv[1])[:6])

    result = {
        "usd_today": round(usd_today, 2),
        "usd_week": round(usd_week, 2),
        "usd_month": round(usd_month, 2),
        "daily": daily,
        "by_service": {k: round(v, 2) for k, v in top_services.items()},
    }
    _cache["data"] = result
    _cache["fetched_at"] = now
    return result
