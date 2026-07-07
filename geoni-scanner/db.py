"""
GEONI - Supabase database integration
Saves audit results and brand check results to Supabase.
Uses service role key to bypass RLS.
"""

import asyncio
import os
import logging
from datetime import datetime, timedelta, timezone
import httpx

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def _headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


async def get_total_scan_count() -> int:
    """Public aggregate count for the landing page social-proof counter (Madde 3.1)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return 0
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{SUPABASE_URL}/rest/v1/audits?select=id",
                headers={**_headers(), "Prefer": "count=exact", "Range": "0-0"},
                timeout=10,
            )
            content_range = r.headers.get("content-range", "")
            total = content_range.split("/")[-1] if "/" in content_range else ""
            return int(total) if total.isdigit() else 0
    except Exception as e:
        logger.warning(f"get_total_scan_count failed: {e}")
        return 0


async def save_audit(job_id: str, request_data: dict, result: dict, user_id: str = None) -> bool:
    """Save domain audit result to Supabase audits table."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logger.warning("Supabase not configured, skipping audit save")
        return False

    payload = {
        "id": job_id,
        "user_id": user_id,
        "type": "web",
        "domain": request_data.get("domain"),
        "score": result.get("score"),
        "result_json": result,
        "credits_spent": 10,
        "status": "complete",
        "completed_at": result.get("created_at"),
    }

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/audits",
                headers=_headers(),
                json=payload,
                timeout=10,
            )
            if r.status_code in (200, 201):
                logger.info(f"Audit {job_id} saved to Supabase")
                # Deduct credits if user is logged in
                if user_id:
                    await deduct_credits(user_id, 5, "web_audit", job_id)
                return True
            logger.warning(f"Supabase audit save failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.warning(f"Supabase audit save error: {e}")
    return False


async def save_brand_check(job_id: str, request_data: dict, result: dict, user_id: str = None) -> bool:
    """Save brand check result to Supabase audits table."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logger.warning("Supabase not configured, skipping brand check save")
        return False

    entity_type = request_data.get("type", "person")
    credits = 10

    payload = {
        "id": job_id,
        "user_id": user_id,
        "type": entity_type,
        "name": request_data.get("name"),
        "role": request_data.get("role"),
        "company": request_data.get("company"),
        "location": request_data.get("location"),
        "topic": request_data.get("topic"),
        "linkedin_url": request_data.get("linkedin_url"),
        "score": result.get("score"),
        "result_json": result,
        "credits_spent": credits,
        "status": "complete",
        "completed_at": result.get("created_at"),
    }

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/audits",
                headers=_headers(),
                json=payload,
                timeout=10,
            )
            if r.status_code in (200, 201):
                logger.info(f"Brand check {job_id} saved to Supabase")
                if user_id:
                    await deduct_credits(user_id, credits, f"{entity_type}_check", job_id)
                return True
            logger.warning(f"Supabase brand check save failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.warning(f"Supabase brand check save error: {e}")
    return False


async def deduct_credits(user_id: str, amount: int, description: str, reference_id: str = None) -> bool:
    """Deduct credits from user balance and record transaction."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            # Get current balance
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=credit_balance,total_credits_spent",
                headers=_headers(),
                timeout=10,
            )
            if r.status_code != 200:
                return False
            data = r.json()
            if not data:
                return False
            current_balance = data[0].get("credit_balance", 0)
            current_spent = data[0].get("total_credits_spent") or 0
            if current_balance < amount:
                logger.warning(f"Insufficient credits for user {user_id}: {current_balance} < {amount}")
                return False

            # Update balance
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(),
                json={
                    "credit_balance": current_balance - amount,
                    "total_credits_spent": current_spent + amount,
                },
                timeout=10,
            )

            # Record transaction
            await client.post(
                f"{SUPABASE_URL}/rest/v1/credit_transactions",
                headers=_headers(),
                json={
                    "user_id": user_id,
                    "amount": -amount,
                    "type": "spend",
                    "description": description,
                    "reference_id": reference_id,
                },
                timeout=10,
            )
            logger.info(f"Deducted {amount} credits from user {user_id}")
            return True
    except Exception as e:
        logger.warning(f"Credit deduction error: {e}")
    return False


async def get_user_id_from_token(token: str) -> str | None:
    """Validate Supabase JWT token and return user ID."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not token:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {token}",
                },
                timeout=10,
            )
            if r.status_code == 200:
                return r.json().get("id")
    except Exception as e:
        logger.warning(f"Token validation error: {e}")
    return None


async def check_is_premium(user_id: str) -> bool:
    """Check if user is admin or has purchased credits (premium)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=is_admin,total_credits_purchased",
                headers=_headers(),
                timeout=8,
            )
            if r.status_code == 200:
                data = r.json()
                if data:
                    return data[0].get('is_admin', False) or data[0].get('total_credits_purchased', 0) > 0
    except Exception as e:
        logger.warning(f"Premium check failed: {e}")
    return False


async def is_strict_admin(user_id: str) -> bool:
    """Strict is_admin check (unlike check_is_premium, does NOT pass for paying non-admin users). Used to gate the admin panel."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=is_admin",
                headers=_headers(),
                timeout=8,
            )
            if r.status_code == 200:
                data = r.json()
                if data:
                    return bool(data[0].get('is_admin', False))
    except Exception as e:
        logger.warning(f"Admin check failed: {e}")
    return False


async def log_provider_call(provider: str) -> None:
    """Fire-and-forget usage counter for external AI provider calls (admin panel 'motor kullanimi' tab).
    Requires the provider_usage table (see admin panel migration) - silently no-ops if missing."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/provider_usage",
                headers=_headers(),
                json={"provider": provider},
                timeout=5,
            )
    except Exception as e:
        logger.debug(f"Provider usage log skipped: {e}")


async def _fetch_all_auth_emails(max_pages: int = 5, per_page: int = 200) -> dict:
    """id -> email map via Supabase GoTrue admin API (profiles table has no email column)."""
    emails = {}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return emails
    try:
        async with httpx.AsyncClient() as client:
            for page in range(1, max_pages + 1):
                r = await client.get(
                    f"{SUPABASE_URL}/auth/v1/admin/users?page={page}&per_page={per_page}",
                    headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
                    timeout=10,
                )
                if r.status_code != 200:
                    break
                batch = r.json().get("users", [])
                for u in batch:
                    emails[u["id"]] = u.get("email", "")
                if len(batch) < per_page:
                    break
    except Exception as e:
        logger.warning(f"auth admin users fetch failed: {e}")
    return emails


async def _count(query: str) -> int:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return 0
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/{query}",
                headers={**_headers(), "Prefer": "count=exact", "Range": "0-0"},
                timeout=10,
            )
            cr = r.headers.get("content-range", "")
            total = cr.split("/")[-1] if "/" in cr else ""
            return int(total) if total.isdigit() else 0
    except Exception:
        return 0


async def _returning_users(activity_since: str, signup_before: str) -> int:
    """Users active (scanned) since `activity_since` whose profile predates
    `signup_before` (as opposed to a brand-new signup scanning for the first
    time). `signup_before` must be the SAME fixed cutoff (today_start) across
    both the "today" and "this week" calls - otherwise the two counts use
    different definitions of "new" and today's number can exceed the week's,
    which reads as a bug (today's activity is a subset of the week's, so its
    returning-count must be too)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return 0
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits?select=user_id&created_at=gte.{activity_since}&limit=5000",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200:
                return 0
            active_ids = {row["user_id"] for row in r.json() if row.get("user_id")}
            if not active_ids:
                return 0
            r2 = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?select=id,created_at&id=in.({','.join(active_ids)})",
                headers=_headers(), timeout=10,
            )
            if r2.status_code != 200:
                return 0
            return sum(1 for row in r2.json() if (row.get("created_at") or "") < signup_before)
    except Exception as e:
        logger.warning(f"returning_users error: {e}")
        return 0


async def get_admin_summary() -> dict:
    """Cheapest possible admin panel numbers, run concurrently so this
    endpoint answers fast while the heavier widgets (charts, external API
    calls) load independently on their own endpoints."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    week_start = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    (total_users, total_audits, new_users_today, returning_users_today,
     new_users_week, returning_users_week) = await asyncio.gather(
        _count("profiles?select=id"),
        _count("audits?select=id"),
        _count(f"profiles?select=id&created_at=gte.{today_start}"),
        _returning_users(today_start, today_start),
        _count(f"profiles?select=id&created_at=gte.{week_start}"),
        _returning_users(week_start, today_start),
    )
    return {
        "total_users": total_users,
        "total_audits": total_audits,
        "new_users_today": new_users_today,
        "returning_users_today": returning_users_today,
        "new_users_week": new_users_week,
        "returning_users_week": returning_users_week,
    }


async def get_admin_scans_daily(days: int = 14) -> dict:
    """Daily scan counts by type, for the overview chart. Also derives
    today/week totals from the same rows instead of firing separate counts."""
    empty = {"days": [], "today": 0, "week": 0}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return empty

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits?select=created_at,type&created_at=gte.{since}&order=created_at.asc&limit=5000",
                headers=_headers(), timeout=15,
            )
            rows = r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"admin_scans_daily error: {e}")
        return empty

    buckets = {}
    for row in rows:
        date_key = (row.get("created_at") or "")[:10]
        if not date_key:
            continue
        t = row.get("type") or "web"
        if t not in ("web", "person", "brand"):
            t = "web"
        buckets.setdefault(date_key, {"web": 0, "person": 0, "brand": 0})[t] += 1

    now = datetime.now(timezone.utc)
    ordered_days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    series = [{"date": d, **buckets.get(d, {"web": 0, "person": 0, "brand": 0})} for d in ordered_days]

    today_key = now.strftime("%Y-%m-%d")
    week_since_key = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    today_total = sum(buckets.get(today_key, {}).values())
    week_total = sum(sum(v.values()) for k, v in buckets.items() if k >= week_since_key)

    return {"days": series, "today": today_total, "week": week_total}


async def get_admin_credits_stats(days: int = 14) -> dict:
    """Purchased/spent/gifted totals plus a daily granted-vs-spent trend and a
    breakdown of spend by reason (web/person/brand scan, admin adjustment).
    Admin users are internal/test accounts, not real customers - their
    activity is excluded from purchased/spent/gifted and the trend/reason
    breakdown, and reported separately (admin_spent) instead."""
    result = {"purchased": 0, "spent": 0, "gifted": 0, "admin_spent": 0, "daily": [], "by_reason": {}}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return result

    admin_ids = set()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?select=id,total_credits_purchased,total_credits_spent,total_credits_gifted,is_admin",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                for row in r.json():
                    if row.get("is_admin"):
                        admin_ids.add(row["id"])
                        result["admin_spent"] += row.get("total_credits_spent") or 0
                    else:
                        result["purchased"] += row.get("total_credits_purchased") or 0
                        result["spent"] += row.get("total_credits_spent") or 0
                        result["gifted"] += row.get("total_credits_gifted") or 0
    except Exception as e:
        logger.warning(f"admin_credits totals error: {e}")

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/credit_transactions?select=amount,type,description,created_at,user_id&created_at=gte.{since}&order=created_at.asc&limit=5000",
                headers=_headers(), timeout=15,
            )
            rows = r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"admin_credits daily error: {e}")
        rows = []

    daily_buckets = {}
    reason_totals = {}
    for row in rows:
        if row.get("user_id") in admin_ids:
            continue
        date_key = (row.get("created_at") or "")[:10]
        amount = row.get("amount") or 0
        if date_key:
            b = daily_buckets.setdefault(date_key, {"granted": 0, "spent": 0})
            if amount >= 0:
                b["granted"] += amount
            else:
                b["spent"] += -amount
        if amount < 0:
            reason = row.get("description") or row.get("type") or "diger"
            reason_totals[reason] = reason_totals.get(reason, 0) + (-amount)

    now = datetime.now(timezone.utc)
    ordered_days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    result["daily"] = [{"date": d, **daily_buckets.get(d, {"granted": 0, "spent": 0})} for d in ordered_days]
    result["by_reason"] = reason_totals
    return result


async def get_credit_packages(active_only: bool = True) -> list:
    """Purchasable credit packages (Lemon Squeezy variants) for the Buy
    Credits page."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            url = f"{SUPABASE_URL}/rest/v1/credit_packages?select=*&order=credits.asc"
            if active_only:
                url += "&is_active=eq.true"
            r = await client.get(url, headers=_headers(), timeout=10)
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"get_credit_packages error: {e}")
    return []


async def record_purchase(user_id: str, credits: int, amount_paid: float, currency_paid: str, external_id: str, channel: str = "web") -> bool:
    """Credits a user's balance for a REAL payment (Lemon Squeezy webhook).
    Idempotent on external_id - a retried/duplicate webhook delivery for
    the same order is a no-op, not a double-credit."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not credits:
        return False
    try:
        async with httpx.AsyncClient() as client:
            dup = await client.get(
                f"{SUPABASE_URL}/rest/v1/credit_transactions?select=id&external_id=eq.{external_id}",
                headers=_headers(), timeout=10,
            )
            if dup.status_code == 200 and dup.json():
                logger.info(f"record_purchase: external_id {external_id} already recorded, skipping")
                return True

            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=credit_balance,total_credits_purchased",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return False
            row = r.json()[0]
            new_balance = (row.get("credit_balance") or 0) + credits
            new_purchased = (row.get("total_credits_purchased") or 0) + credits
            patch_r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(),
                json={"credit_balance": new_balance, "total_credits_purchased": new_purchased},
                timeout=10,
            )
            if patch_r.status_code not in (200, 204):
                return False

            await client.post(
                f"{SUPABASE_URL}/rest/v1/credit_transactions",
                headers=_headers(),
                json={
                    "user_id": user_id,
                    "amount": credits,
                    "type": "purchase",
                    "description": "Lemon Squeezy satın alma",
                    "channel": channel,
                    "amount_paid": amount_paid,
                    "currency_paid": currency_paid,
                    "external_id": external_id,
                },
                timeout=10,
            )
            return True
    except Exception as e:
        logger.warning(f"record_purchase error: {e}")
    return False


async def get_admin_sales_stats(days: int = 14) -> dict:
    """Real revenue (from actual Lemon Squeezy purchases), broken down by
    channel (web/ios/android) and by signup traffic source (utm_source),
    plus a list of recent purchases for the Satış tab."""
    result = {"revenue_by_channel": {}, "revenue_total": 0, "currency": "TRY", "by_source": {}, "recent": [], "daily": []}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return result

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/credit_transactions"
                f"?select=user_id,amount,channel,amount_paid,currency_paid,created_at"
                f"&type=eq.purchase&created_at=gte.{since}&order=created_at.desc&limit=1000",
                headers=_headers(), timeout=15,
            )
            purchases = r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"get_admin_sales_stats purchases error: {e}")
        purchases = []

    daily_buckets = {}
    for p in purchases:
        channel = p.get("channel") or "web"
        amount_paid = float(p.get("amount_paid") or 0)
        result["revenue_by_channel"][channel] = result["revenue_by_channel"].get(channel, 0) + amount_paid
        result["revenue_total"] += amount_paid
        if p.get("currency_paid"):
            result["currency"] = p["currency_paid"]
        date_key = (p.get("created_at") or "")[:10]
        if date_key:
            daily_buckets[date_key] = daily_buckets.get(date_key, 0) + amount_paid
    result["recent"] = purchases[:20]
    ordered_days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    result["daily"] = [{"date": d, "revenue": round(daily_buckets.get(d, 0), 2)} for d in ordered_days]

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?select=utm_source&created_at=gte.{since}",
                headers=_headers(), timeout=15,
            )
            profiles = r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"get_admin_sales_stats sources error: {e}")
        profiles = []

    by_source = {}
    for p in profiles:
        source = p.get("utm_source") or "direct"
        by_source[source] = by_source.get(source, 0) + 1
    result["by_source"] = by_source

    return result


async def get_pricing_tiers() -> list:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/credit_pricing_tiers?select=*&order=platform.asc,min_credits.asc",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"get_pricing_tiers error: {e}")
    return []


async def add_pricing_tier(platform: str, min_credits: int, max_credits: int | None, price_per_credit: float, currency: str = "TRY") -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/credit_pricing_tiers",
                headers=_headers(),
                json={
                    "platform": platform, "min_credits": min_credits, "max_credits": max_credits,
                    "price_per_credit": price_per_credit, "currency": currency,
                },
                timeout=10,
            )
            return r.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"add_pricing_tier error: {e}")
    return False


async def delete_pricing_tier(tier_id: str) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.delete(
                f"{SUPABASE_URL}/rest/v1/credit_pricing_tiers?id=eq.{tier_id}",
                headers=_headers(), timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"delete_pricing_tier error: {e}")
    return False


async def get_admin_provider_usage() -> dict:
    """Call-count fallback for the 4 external AI motors (see anthropic_admin.py
    for the one motor - Anthropic - that also has real USD cost data)."""
    empty = {"today": {}, "week": {}}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return empty

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    week_start = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    provider_usage = {"today": {}, "week": {}}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/provider_usage?select=provider,created_at&created_at=gte.{week_start}&order=created_at.desc&limit=5000",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                for row in r.json():
                    p = row.get("provider", "unknown")
                    provider_usage["week"][p] = provider_usage["week"].get(p, 0) + 1
                    if row.get("created_at", "") >= today_start:
                        provider_usage["today"][p] = provider_usage["today"].get(p, 0) + 1
            else:
                logger.info(f"provider_usage query failed ({r.status_code}) - table may not exist yet")
    except Exception as e:
        logger.warning(f"Provider usage aggregate failed: {e}")
    return provider_usage


async def get_manual_balances() -> dict:
    """Manually-entered real balances for providers with no balance API
    (OpenAI, Google, Perplexity, Tavily) - keyed by provider."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/manual_balances?select=provider,balance,currency,updated_at",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                return {row["provider"]: row for row in r.json()}
            logger.info(f"manual_balances query failed ({r.status_code}) - table may not exist yet")
    except Exception as e:
        logger.warning(f"get_manual_balances error: {e}")
    return {}


async def set_manual_balance(provider: str, balance: float, currency: str = "USD") -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/manual_balances",
                headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
                json={
                    "provider": provider,
                    "balance": balance,
                    "currency": currency,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                timeout=10,
            )
            return r.status_code in (200, 201, 204)
    except Exception as e:
        logger.warning(f"set_manual_balance error: {e}")
    return False


async def log_perplexity_usage(cost_usd: float, prompt_tokens: int, completion_tokens: int) -> None:
    """Fire-and-forget cost log for Perplexity calls. Perplexity has no cost/usage
    API at all (unlike OpenAI/Anthropic) - GEONI computes cost itself from the
    token counts already returned in every response, using published per-token
    + per-request pricing (see perplexity_admin.py). Requires the
    perplexity_usage_log table - silently no-ops if missing."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/perplexity_usage_log",
                headers=_headers(),
                json={
                    "cost_usd": cost_usd,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                },
                timeout=5,
            )
    except Exception as e:
        logger.debug(f"Perplexity usage log skipped: {e}")


async def get_perplexity_cost_daily(start: datetime, end: datetime) -> dict:
    """date (YYYY-MM-DD) -> USD cost that day, summed from GEONI's own
    perplexity_usage_log rows (self-computed, since Perplexity has no cost API)."""
    daily = {}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return daily
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/perplexity_usage_log"
                f"?select=cost_usd,created_at"
                f"&created_at=gte.{start.strftime('%Y-%m-%dT%H:%M:%SZ')}"
                f"&created_at=lt.{end.strftime('%Y-%m-%dT%H:%M:%SZ')}",
                headers=_headers(), timeout=15,
            )
            if r.status_code == 200:
                for row in r.json():
                    date_key = (row.get("created_at") or "")[:10]
                    if not date_key:
                        continue
                    daily[date_key] = daily.get(date_key, 0) + float(row.get("cost_usd") or 0)
            else:
                logger.info(f"perplexity_usage_log query failed ({r.status_code}) - table may not exist yet")
    except Exception as e:
        logger.warning(f"get_perplexity_cost_daily error: {e}")
    return daily


async def get_manual_topups_total(provider: str) -> float:
    """Sum of all logged top-ups for a provider (e.g. openai) - paired with
    the provider's real Costs API spend to estimate remaining balance,
    since top-ups happen repeatedly over time rather than as one fixed value."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return 0.0
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/manual_topups?select=amount&provider=eq.{provider}",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                return sum(float(row.get("amount") or 0) for row in r.json())
            logger.info(f"manual_topups query failed ({r.status_code}) - table may not exist yet")
    except Exception as e:
        logger.warning(f"get_manual_topups_total error: {e}")
    return 0.0


async def list_manual_topups(provider: str, limit: int = 20) -> list:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/manual_topups?select=id,amount,note,created_at&provider=eq.{provider}&order=created_at.desc&limit={limit}",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"list_manual_topups error: {e}")
    return []


async def add_manual_topup(provider: str, amount: float, note: str = "") -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/manual_topups",
                headers=_headers(),
                json={"provider": provider, "amount": amount, "note": note},
                timeout=10,
            )
            return r.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"add_manual_topup error: {e}")
    return False


async def get_manual_cost(provider: str) -> dict | None:
    """Latest manually-logged cost snapshot for a provider that has no real
    cost API (e.g. Supabase - its Management API only exposes request
    counts, not dollar billing). Unlike manual_topups (which accumulates),
    this is a single current/projected snapshot the admin re-enters
    periodically from the provider's own billing dashboard."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/manual_costs"
                f"?select=*&provider=eq.{provider}&order=created_at.desc&limit=1",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200 and r.json():
                return r.json()[0]
            if r.status_code != 200:
                logger.info(f"manual_costs query failed ({r.status_code}) - table may not exist yet")
    except Exception as e:
        logger.warning(f"get_manual_cost error: {e}")
    return None


async def set_manual_cost(provider: str, current_cost: float, projected_cost: float = None,
                           cycle_start: str = None, cycle_end: str = None, note: str = "") -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/manual_costs",
                headers=_headers(),
                json={
                    "provider": provider, "current_cost": current_cost, "projected_cost": projected_cost,
                    "cycle_start": cycle_start, "cycle_end": cycle_end, "note": note,
                },
                timeout=10,
            )
            return r.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"set_manual_cost error: {e}")
    return False


async def admin_list_users(search: str = "", limit: int = 50, offset: int = 0) -> dict:
    """Merges profiles with auth emails (profiles has no email column). Search/pagination done in-process - fine at MVP scale."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"users": [], "total": 0}

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?select=id,full_name,credit_balance,total_credits_purchased,total_credits_spent,total_credits_gifted,is_admin,created_at&order=created_at.desc&limit=1000",
                headers=_headers(), timeout=15,
            )
            profiles = r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"admin_list_users profiles fetch failed: {e}")
        profiles = []

    emails = await _fetch_all_auth_emails()
    for p in profiles:
        p["email"] = emails.get(p["id"], "")

    if search:
        s = search.lower()
        profiles = [p for p in profiles if s in (p.get("email") or "").lower() or s in (p.get("full_name") or "").lower()]

    total = len(profiles)
    return {"users": profiles[offset:offset + limit], "total": total}


async def admin_adjust_credits(user_id: str, delta: int, reason: str = "") -> bool:
    """Manual credit grant (positive delta) or deduction (negative delta) by an admin.
    Grants are tracked in total_credits_gifted, NOT total_credits_purchased - a gift
    isn't revenue, and once a real payment flow exists, purchased-based earnings
    calculations must not count these."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not delta:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=credit_balance,total_credits_gifted",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return False
            row = r.json()[0]
            new_balance = max(0, row.get("credit_balance", 0) + delta)
            update = {"credit_balance": new_balance}
            if delta > 0:
                update["total_credits_gifted"] = (row.get("total_credits_gifted") or 0) + delta

            patch_r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(), json=update, timeout=10,
            )
            if patch_r.status_code not in (200, 204):
                return False

            await client.post(
                f"{SUPABASE_URL}/rest/v1/credit_transactions",
                headers=_headers(),
                json={
                    "user_id": user_id,
                    "amount": delta,
                    "type": "admin_grant" if delta > 0 else "admin_deduct",
                    "description": reason or "Admin manuel duzeltme",
                },
                timeout=10,
            )
            return True
    except Exception as e:
        logger.warning(f"admin_adjust_credits error: {e}")
    return False


async def admin_set_is_admin(user_id: str, is_admin_flag: bool) -> bool:
    """Grant or revoke admin panel access for a user."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(), json={"is_admin": is_admin_flag}, timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"admin_set_is_admin error: {e}")
    return False


_AUDIT_SORT_FIELDS = {"email", "type", "target", "score", "credits_spent", "created_at"}


def _audit_sort_key(a: dict, field: str):
    if field == "email":
        return (a.get("email") or "").lower()
    if field == "target":
        return (a.get("domain") or a.get("name") or "").lower()
    if field == "score":
        return a.get("score") if a.get("score") is not None else -1
    if field == "credits_spent":
        return a.get("credits_spent") or 0
    if field == "type":
        return a.get("type") or ""
    return a.get("created_at") or ""


async def admin_list_audits(
    search: str = "", sort_by: str = "created_at", sort_dir: str = "desc", limit: int = 50, offset: int = 0
) -> dict:
    """Full cross-user audit/brand-check log for the admin panel.
    Search/sort/pagination done in-process (mirrors admin_list_users) -
    email lives in Supabase Auth, not the audits table, so it can't be
    filtered/sorted via a plain PostgREST query anyway."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"audits": [], "total": 0}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits?select=id,user_id,type,domain,name,score,credits_spent,status,created_at&order=created_at.desc&limit=2000",
                headers=_headers(),
                timeout=15,
            )
            audits = r.json() if r.status_code == 200 else []

        emails = await _fetch_all_auth_emails()
        for a in audits:
            a["email"] = emails.get(a.get("user_id"), "") if a.get("user_id") else ""

        if search:
            s = search.lower()
            audits = [
                a for a in audits
                if s in (a.get("email") or "").lower()
                or s in (a.get("domain") or "").lower()
                or s in (a.get("name") or "").lower()
            ]

        sort_field = sort_by if sort_by in _AUDIT_SORT_FIELDS else "created_at"
        audits.sort(key=lambda a: _audit_sort_key(a, sort_field), reverse=(sort_dir != "asc"))

        total = len(audits)
        return {"audits": audits[offset:offset + limit], "total": total}
    except Exception as e:
        logger.warning(f"admin_list_audits error: {e}")
        return {"audits": [], "total": 0}
