"""
GEONI - Gemini real cost via GCP Billing Export to BigQuery.

Unlike OpenAI/Anthropic, Google's Generative Language API (Gemini) has no
direct cost/usage REST API. The only way to get real spend is GCP's Cloud
Billing "detailed usage cost" export to BigQuery (enabled 2026-07,
project extreme-lattice-229211, dataset geoni1), queried here with a
read-only service account (GCP_BILLING_SA_KEY secret - BigQuery Data Viewer
+ BigQuery Job User roles only).

Billing export data lags real usage by up to ~24h, so "today" may be
incomplete/zero even with real spend happening - this is a BigQuery/export
limitation, not a bug here.

The billing account has a "Prepay - AI Studio" ledger (a real prepaid
credit balance, unlike AWS's pure postpaid model) - amounts are in the
account's own currency (TRY), not USD. The admin panel pairs this real
cost with a manually-logged top-up total (like OpenAI/Anthropic) to
estimate remaining balance.
"""

import os
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from google.oauth2 import service_account
import google.auth.transport.requests

logger = logging.getLogger(__name__)

GCP_BILLING_SA_KEY = os.environ.get("GCP_BILLING_SA_KEY", "")
GCP_PROJECT_ID = "extreme-lattice-229211"
BQ_DATASET = "geoni1"

_SCOPES = ["https://www.googleapis.com/auth/bigquery.readonly"]

_token_cache = {"token": None, "expiry": None}
_table_cache = {"name": None, "fetched_at": None}
_TABLE_CACHE_TTL = timedelta(hours=24)

ALL_TIME_LOOKBACK_DAYS = 120

_summary_cache = {"value": None, "fetched_at": None}
_SUMMARY_CACHE_TTL = timedelta(minutes=15)  # BigQuery queries aren't free - cache longer than the others


def _get_access_token() -> str | None:
    if not GCP_BILLING_SA_KEY:
        return None
    now = datetime.now(timezone.utc)
    if _token_cache["token"] and _token_cache["expiry"] and now < _token_cache["expiry"]:
        return _token_cache["token"]
    try:
        info = json.loads(GCP_BILLING_SA_KEY)
        creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
        creds.refresh(google.auth.transport.requests.Request())
        _token_cache["token"] = creds.token
        expiry = creds.expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        _token_cache["expiry"] = expiry - timedelta(minutes=2)
        return creds.token
    except Exception as e:
        logger.warning(f"GCP billing auth failed: {e}")
        return None


async def _run_query(sql: str, params: list | None = None) -> list[dict]:
    token = _get_access_token()
    if not token:
        return []
    body = {"query": sql, "useLegacySql": False, "timeoutMs": 20000}
    if params:
        body["queryParameters"] = params
        body["parameterMode"] = "NAMED"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://bigquery.googleapis.com/bigquery/v2/projects/{GCP_PROJECT_ID}/queries",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body,
                timeout=30,
            )
            if r.status_code != 200:
                logger.warning(f"BigQuery query failed: {r.status_code} {r.text[:300]}")
                return []
            data = r.json()
            rows = data.get("rows", [])
            if not rows:
                return []
            fields = [f["name"] for f in data["schema"]["fields"]]
            return [dict(zip(fields, [c.get("v") for c in row["f"]])) for row in rows]
    except Exception as e:
        logger.warning(f"BigQuery query error: {e}")
        return []


async def _get_table_name() -> str | None:
    """The billing export table name is auto-generated - either
    gcp_billing_export_v1_<ID> (standard usage cost) or
    gcp_billing_export_resource_v1_<ID> (detailed/resource-level usage
    cost, which is what this project's export turned out to be) -
    discovered via INFORMATION_SCHEMA instead of hardcoding either the
    billing account ID or which export type was chosen."""
    now = datetime.now(timezone.utc)
    if _table_cache["name"] and _table_cache["fetched_at"] and now - _table_cache["fetched_at"] < _TABLE_CACHE_TTL:
        return _table_cache["name"]
    rows = await _run_query(
        f"SELECT table_name FROM `{GCP_PROJECT_ID}.{BQ_DATASET}`.INFORMATION_SCHEMA.TABLES "
        f"WHERE table_name LIKE 'gcp_billing_export%v1_%' LIMIT 1"
    )
    if not rows:
        logger.warning("GCP billing export table not found yet in BigQuery dataset")
        return None
    name = rows[0]["table_name"]
    _table_cache["name"] = name
    _table_cache["fetched_at"] = now
    return name


async def _fetch_daily_cost(start: datetime, end: datetime) -> dict:
    """date (YYYY-MM-DD) -> USD cost that day, filtered to the Generative
    Language API (Gemini) service. Service name filter uses LIKE since the
    exact string Google assigns isn't confirmed until real export data
    exists - broadened to catch "Generative Language API" / "Gemini API"."""
    daily = {}
    table = await _get_table_name()
    if not table:
        return daily
    rows = await _run_query(
        f"SELECT DATE(usage_start_time) AS day, SUM(cost) AS cost "
        f"FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.{table}` "
        f"WHERE usage_start_time >= @start_ts AND usage_start_time < @end_ts "
        f"AND (LOWER(service.description) LIKE '%generative language%' OR LOWER(service.description) LIKE '%gemini%') "
        f"GROUP BY day ORDER BY day",
        params=[
            {"name": "start_ts", "parameterType": {"type": "TIMESTAMP"}, "parameterValue": {"value": start.strftime("%Y-%m-%dT%H:%M:%SZ")}},
            {"name": "end_ts", "parameterType": {"type": "TIMESTAMP"}, "parameterValue": {"value": end.strftime("%Y-%m-%dT%H:%M:%SZ")}},
        ],
    )
    for row in rows:
        daily[row["day"]] = float(row.get("cost") or 0)
    return daily


async def get_gemini_cost_summary() -> dict | None:
    """Same shape as openai_admin/anthropic_admin's summaries. Returns None
    if the service account key isn't configured (feature not enabled)."""
    if not GCP_BILLING_SA_KEY:
        return None

    now = datetime.now(timezone.utc)
    if _summary_cache["value"] is not None and _summary_cache["fetched_at"] and now - _summary_cache["fetched_at"] < _SUMMARY_CACHE_TTL:
        return _summary_cache["value"]

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_daily = await _fetch_daily_cost(month_start, now)
    all_time_daily = await _fetch_daily_cost(now - timedelta(days=ALL_TIME_LOOKBACK_DAYS), now)
    usd_all_time = sum(all_time_daily.values())

    today_key = now.strftime("%Y-%m-%d")
    week_start_key = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    usd_today = month_daily.get(today_key, 0)
    usd_week = sum(v for k, v in month_daily.items() if k >= week_start_key)
    usd_month = sum(month_daily.values())
    usd_all_time = max(usd_all_time, usd_month)

    result = {
        "usd_today": round(usd_today, 4),
        "usd_week": round(usd_week, 4),
        "usd_month": round(usd_month, 4),
        "usd_all_time": round(usd_all_time, 4),
        "daily": [{"date": d, "usd": round(v, 4)} for d, v in sorted(month_daily.items())],
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _summary_cache["value"] = result
    _summary_cache["fetched_at"] = now
    return result


_monthly_cache = {"value": None, "fetched_at": None}
_MONTHLY_CACHE_TTL = timedelta(hours=6)


async def get_gemini_monthly_breakdown() -> dict[str, float] | None:
    """TRY cost grouped by calendar month (YYYY-MM) over ALL_TIME_LOOKBACK_DAYS.
    Cached separately (longer TTL) from the 15-min summary cache since
    BigQuery queries aren't free."""
    if not GCP_BILLING_SA_KEY:
        return None
    now = datetime.now(timezone.utc)
    if _monthly_cache["value"] is not None and _monthly_cache["fetched_at"] and now - _monthly_cache["fetched_at"] < _MONTHLY_CACHE_TTL:
        return _monthly_cache["value"]
    daily = await _fetch_daily_cost(now - timedelta(days=ALL_TIME_LOOKBACK_DAYS), now)
    monthly = {}
    for date_key, amount in daily.items():
        month_key = date_key[:7]
        monthly[month_key] = monthly.get(month_key, 0) + amount
    result = {k: round(v, 4) for k, v in monthly.items()}
    _monthly_cache["value"] = result
    _monthly_cache["fetched_at"] = now
    return result
