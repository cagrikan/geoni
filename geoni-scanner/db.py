"""
GEONI - Supabase database integration
Saves audit results and brand check results to Supabase.
Uses service role key to bypass RLS.
"""

import asyncio
import os
import time
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs
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


_token_cache: dict[str, tuple[str | None, float]] = {}
_TOKEN_CACHE_TTL = 30.0  # seconds


async def get_user_id_from_token(token: str) -> str | None:
    """Validate Supabase JWT token and return user ID. The admin panel opens
    with a burst of ~10-15 parallel requests (one per widget), each calling
    this with the SAME token - without caching, that's 10-15 concurrent hits
    to Supabase's /auth/v1/user, which under load turned single-digit-ms
    checks into multi-second ones (contention, not raw latency). A short
    cache collapses the burst into one real validation."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not token:
        return None
    cached = _token_cache.get(token)
    if cached and time.monotonic() - cached[1] < _TOKEN_CACHE_TTL:
        return cached[0]
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
                user_id = r.json().get("id")
                _token_cache[token] = (user_id, time.monotonic())
                return user_id
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


_is_admin_cache: dict[str, tuple[bool, float]] = {}


async def is_strict_admin(user_id: str) -> bool:
    """Strict is_admin check (unlike check_is_premium, does NOT pass for paying
    non-admin users). Used to gate the admin panel - cached briefly for the
    same reason as get_user_id_from_token (admin panel load fires this many
    times concurrently for the same user)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return False
    cached = _is_admin_cache.get(user_id)
    if cached and time.monotonic() - cached[1] < _TOKEN_CACHE_TTL:
        return cached[0]
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
                    result = bool(data[0].get('is_admin', False))
                    _is_admin_cache[user_id] = (result, time.monotonic())
                    return result
    except Exception as e:
        logger.warning(f"Admin check failed: {e}")
    return False


async def is_expert(user_id: str) -> bool:
    """Gates the expert ticket panel - separate from is_admin, since ticket
    experts shouldn't automatically get full admin panel access."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=is_expert",
                headers=_headers(),
                timeout=8,
            )
            if r.status_code == 200:
                data = r.json()
                if data:
                    return bool(data[0].get('is_expert', False))
    except Exception as e:
        logger.warning(f"Expert check failed: {e}")
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


_auth_emails_cache = {"value": None, "fetched_at": None}
_AUTH_EMAILS_CACHE_TTL = timedelta(minutes=5)


async def _fetch_all_auth_emails(max_pages: int = 5, per_page: int = 200) -> dict:
    """id -> email map via Supabase GoTrue admin API (profiles table has no email
    column). This is up to 5 sequential paginated HTTP calls to Supabase - both
    admin_list_users and admin_list_audits called this on every single request
    (every keystroke in search, every sort click, every page turn), which is
    what made both admin panel tabs feel slow. Emails change rarely, so a short
    cache turns that into one real fetch every few minutes instead of every click."""
    now = datetime.now(timezone.utc)
    if _auth_emails_cache["value"] is not None and _auth_emails_cache["fetched_at"] and now - _auth_emails_cache["fetched_at"] < _AUTH_EMAILS_CACHE_TTL:
        return _auth_emails_cache["value"]
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
        return _auth_emails_cache["value"] or emails
    _auth_emails_cache["value"] = emails
    _auth_emails_cache["fetched_at"] = now
    return emails


async def _fetch_auth_user(user_id: str) -> dict | None:
    """Single-user GoTrue admin lookup - used for last_sign_in_at on the
    user detail card (not worth bulk-caching like the email map, since it's
    only fetched when an admin actually opens one user's detail view)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
                headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
                timeout=10,
            )
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"_fetch_auth_user error: {e}")
    return None


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
    channel (web/ios/android), by signup traffic source (utm_source, i.e.
    how many people SIGNED UP from each source), and by traffic source's
    actual REVENUE (i.e. of the people who bought, which source brought
    them - the number that actually answers "is this channel worth it"),
    plus a list of recent purchases for the Satış tab."""
    result = {
        "revenue_by_channel": {}, "revenue_total": 0, "currency": "TRY",
        "by_source": {}, "revenue_by_source": {}, "recent": [], "daily": [],
    }
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

    # Revenue by traffic source: for each buyer in this window, look up their
    # (own signup-time) utm_source regardless of when they signed up - a
    # purchase this week can come from someone who signed up via Instagram
    # last month, and that's exactly the number worth seeing.
    buyer_ids = sorted({p["user_id"] for p in purchases if p.get("user_id")})
    if buyer_ids:
        try:
            ids_filter = ",".join(buyer_ids)
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/profiles?select=id,utm_source&id=in.({ids_filter})",
                    headers=_headers(), timeout=15,
                )
                buyer_profiles = r.json() if r.status_code == 200 else []
        except Exception as e:
            logger.warning(f"get_admin_sales_stats buyer sources error: {e}")
            buyer_profiles = []
        source_by_user = {bp["id"]: (bp.get("utm_source") or "direct") for bp in buyer_profiles}
        revenue_by_source = {}
        for p in purchases:
            source = source_by_user.get(p.get("user_id"), "direct")
            revenue_by_source[source] = revenue_by_source.get(source, 0) + float(p.get("amount_paid") or 0)
        result["revenue_by_source"] = {k: round(v, 2) for k, v in revenue_by_source.items()}

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


async def list_campaigns() -> list:
    """Admin-managed short links (geoni.ai/r/<slug>) that redirect to a
    target URL with baked-in UTM params - used for things like an Instagram
    bio link, so the destination doesn't need a long query string."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/campaigns?select=*&order=created_at.desc",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"list_campaigns error: {e}")
    return []


async def create_campaign(slug: str, name: str, target_url: str, utm_source: str, utm_medium: str, utm_campaign: str = "") -> dict:
    """Returns {"success": bool, "error": str|None} - slug must be globally
    unique (enforced by a DB constraint), so a duplicate slug fails cleanly
    with a message the admin panel can show instead of a generic error."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not configured"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/campaigns",
                headers=_headers(),
                json={
                    "slug": slug, "name": name, "target_url": target_url,
                    "utm_source": utm_source, "utm_medium": utm_medium,
                    "utm_campaign": utm_campaign or None,
                },
                timeout=10,
            )
            if r.status_code in (200, 201):
                return {"success": True, "error": None}
            if r.status_code == 409:
                return {"success": False, "error": "duplicate_slug"}
            return {"success": False, "error": f"http_{r.status_code}"}
    except Exception as e:
        logger.warning(f"create_campaign error: {e}")
        return {"success": False, "error": "exception"}


async def delete_campaign(campaign_id: str) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.delete(
                f"{SUPABASE_URL}/rest/v1/campaigns?id=eq.{campaign_id}",
                headers=_headers(), timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"delete_campaign error: {e}")
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


_profiles_cache = {"value": None, "fetched_at": None}
_LIST_CACHE_TTL = timedelta(seconds=20)


_USER_SORT_FIELDS = {"email", "credit_balance", "total_credits_purchased", "total_credits_spent", "total_credits_gifted", "created_at"}


def _user_sort_key(p: dict, field: str):
    if field == "email":
        return (p.get("email") or "").lower()
    if field == "created_at":
        return p.get("created_at") or ""
    return p.get(field) or 0


async def admin_list_users(search: str = "", sort_by: str = "created_at", sort_dir: str = "desc", limit: int = 50, offset: int = 0) -> dict:
    """Merges profiles with auth emails (profiles has no email column). Search/
    sort/pagination done in-process - fine at MVP scale. The full profile list
    is cached briefly so typing in the search box or flipping pages doesn't
    re-fetch all 1000 rows from Supabase on every keystroke."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"users": [], "total": 0}

    now = datetime.now(timezone.utc)
    if _profiles_cache["value"] is not None and _profiles_cache["fetched_at"] and now - _profiles_cache["fetched_at"] < _LIST_CACHE_TTL:
        profiles = [dict(p) for p in _profiles_cache["value"]]
    else:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/profiles?select=id,full_name,credit_balance,total_credits_purchased,total_credits_spent,total_credits_gifted,is_admin,is_expert,is_suspended,created_at&order=created_at.desc&limit=1000",
                    headers=_headers(), timeout=15,
                )
                profiles = r.json() if r.status_code == 200 else []
        except Exception as e:
            logger.warning(f"admin_list_users profiles fetch failed: {e}")
            profiles = []
        _profiles_cache["value"] = profiles
        _profiles_cache["fetched_at"] = now

    emails = await _fetch_all_auth_emails()
    for p in profiles:
        p["email"] = emails.get(p["id"], "")

    if search:
        s = search.lower()
        profiles = [p for p in profiles if s in (p.get("email") or "").lower() or s in (p.get("full_name") or "").lower()]

    sort_field = sort_by if sort_by in _USER_SORT_FIELDS else "created_at"
    profiles.sort(key=lambda p: _user_sort_key(p, sort_field), reverse=(sort_dir != "asc"))

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


_ADMIN_SCOPE_FIELDS = {"users", "tickets", "campaigns"}


_admin_scope_cache: dict[str, tuple[bool, float]] = {}


async def has_admin_scope(user_id: str, scope: str) -> bool:
    """Narrower than is_strict_admin: also requires the specific
    admin_scope_<scope> flag, so a full admin can hand a limited admin
    (e.g. just ticket operations) without giving them everything. Cached
    briefly like is_strict_admin - a tab with several widgets fires this
    concurrently for the same user+scope."""
    if scope not in _ADMIN_SCOPE_FIELDS or not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return False
    cache_key = f"{user_id}:{scope}"
    cached = _admin_scope_cache.get(cache_key)
    if cached and time.monotonic() - cached[1] < _TOKEN_CACHE_TTL:
        return cached[0]
    field = f"admin_scope_{scope}"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=is_admin,{field}",
                headers=_headers(), timeout=8,
            )
            if r.status_code == 200 and r.json():
                row = r.json()[0]
                result = bool(row.get("is_admin")) and bool(row.get(field))
                _admin_scope_cache[cache_key] = (result, time.monotonic())
                return result
    except Exception as e:
        logger.warning(f"has_admin_scope error: {e}")
    return False


async def admin_set_admin_scopes(user_id: str, scopes: dict) -> bool:
    """scopes: {"users": bool, "tickets": bool, "campaigns": bool} - only
    known fields are ever written, so an unexpected key can't add an
    arbitrary column to the PATCH payload."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    payload = {f"admin_scope_{k}": bool(v) for k, v in scopes.items() if k in _ADMIN_SCOPE_FIELDS}
    if not payload:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(), json=payload, timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"admin_set_admin_scopes error: {e}")
    return False


async def admin_get_user_detail(user_id: str) -> dict | None:
    """Profile + expert verified/rejected counts for the admin panel's user
    detail view. Recent scans/transactions/tickets are separate paginated
    endpoints (admin_get_user_audits/transactions/tickets) - bundling them
    here would mean this single call could never be paginated per-list."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            profile_r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=*",
                headers=_headers(), timeout=10,
            )
            if profile_r.status_code != 200 or not profile_r.json():
                return None
            profile = profile_r.json()[0]

            expert_stats = None
            if profile.get("is_expert"):
                verified_r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/tickets?assigned_expert_id=eq.{user_id}&status=eq.verified&select=id",
                    headers={**_headers(), "Prefer": "count=exact", "Range": "0-0"}, timeout=10,
                )
                rejected_r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/tickets?assigned_expert_id=eq.{user_id}&status=eq.rejected&select=id",
                    headers={**_headers(), "Prefer": "count=exact", "Range": "0-0"}, timeout=10,
                )
                def _count_from_range(resp):
                    cr = resp.headers.get("content-range", "")
                    total = cr.split("/")[-1] if "/" in cr else ""
                    return int(total) if total.isdigit() else 0
                expert_stats = {"verified": _count_from_range(verified_r), "rejected": _count_from_range(rejected_r)}

        emails = await _fetch_all_auth_emails()
        profile["email"] = emails.get(user_id, "")

        auth_user = await _fetch_auth_user(user_id)
        profile["last_sign_in_at"] = auth_user.get("last_sign_in_at") if auth_user else None

        return {"profile": profile, "expert_stats": expert_stats}
    except Exception as e:
        logger.warning(f"admin_get_user_detail error: {e}")
        return None


async def _paginated_get(url: str, headers: dict) -> tuple[list, int]:
    """Shared helper: PostgREST count=exact + Range pagination -> (rows, total).
    206 is the correct success status for a satisfied Range request (not 200)."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, headers=headers, timeout=10)
            if r.status_code not in (200, 206):
                return [], 0
            rows = r.json()
            cr = r.headers.get("content-range", "")
            total_s = cr.split("/")[-1] if "/" in cr else ""
            total = int(total_s) if total_s.isdigit() else len(rows)
            return rows, total
    except Exception as e:
        logger.warning(f"_paginated_get error ({url}): {e}")
        return [], 0


async def admin_get_user_audits(user_id: str, limit: int = 8, offset: int = 0) -> dict:
    rows, total = await _paginated_get(
        f"{SUPABASE_URL}/rest/v1/audits?user_id=eq.{user_id}&select=id,type,domain,name,score,credits_spent,status,created_at"
        f"&order=created_at.desc&limit={limit}&offset={offset}",
        {**_headers(), "Prefer": "count=exact"},
    )
    return {"items": rows, "total": total}


async def admin_get_user_transactions(user_id: str, limit: int = 8, offset: int = 0) -> dict:
    rows, total = await _paginated_get(
        f"{SUPABASE_URL}/rest/v1/credit_transactions?user_id=eq.{user_id}&select=id,amount,type,description,created_at"
        f"&order=created_at.desc&limit={limit}&offset={offset}",
        {**_headers(), "Prefer": "count=exact"},
    )
    return {"items": rows, "total": total}


async def admin_get_user_tickets(user_id: str, limit: int = 8, offset: int = 0) -> dict:
    rows, total = await _paginated_get(
        f"{SUPABASE_URL}/rest/v1/tickets?user_id=eq.{user_id}&select=id,ticket_type_id,status,token_cost,created_at"
        f"&order=created_at.desc&limit={limit}&offset={offset}",
        {**_headers(), "Prefer": "count=exact"},
    )
    if rows:
        types = await list_ticket_types(active_only=False)
        type_by_id = {t["id"]: t["name"] for t in types}
        for tk in rows:
            tk["ticket_type_name"] = type_by_id.get(tk.get("ticket_type_id"), "")
    return {"items": rows, "total": total}


async def admin_set_user_notes(user_id: str, notes: str) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(), json={"admin_notes": notes}, timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"admin_set_user_notes error: {e}")
    return False


async def admin_set_suspended(user_id: str, suspended: bool) -> bool:
    """Blocks the account from spending credits (brand-check, checkout) -
    checked in main.py's _require_user, so it takes effect immediately on
    every authenticated endpoint that uses it, not just new logins."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(), json={"is_suspended": suspended}, timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"admin_set_suspended error: {e}")
    return False


async def is_user_suspended(user_id: str) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=is_suspended",
                headers=_headers(), timeout=8,
            )
            if r.status_code == 200 and r.json():
                return bool(r.json()[0].get("is_suspended", False))
    except Exception as e:
        logger.warning(f"is_user_suspended error: {e}")
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


_audits_cache = {"value": None, "fetched_at": None}


async def admin_get_audit(audit_id: str) -> dict | None:
    """Full row (including result_json) for one audit - the list endpoints
    deliberately omit result_json (too heavy to send for every row), so the
    admin panel's "view this scan" click fetches it on demand."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits?id=eq.{audit_id}&select=*",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200 and r.json():
                return r.json()[0]
    except Exception as e:
        logger.warning(f"admin_get_audit error: {e}")
    return None


async def get_latest_web_audit_by_domain(domain: str) -> dict | None:
    """En son TAMAMLANMIS 'web' taramasi - llms_robots bilet otomasyonu
    icin marka/konu/sayfa verisini buradan cekiyoruz. Eski taramalarda
    'pages' alani olmayabilir (bu alan sonradan eklendi) - cagiran taraf
    bunu graceful fallback ile ele almali."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits?domain=eq.{domain}&type=eq.web&status=eq.complete"
                f"&select=*&order=created_at.desc&limit=1",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200 and r.json():
                return r.json()[0]
    except Exception as e:
        logger.warning(f"get_latest_web_audit_by_domain error: {e}")
    return None


async def admin_list_audits(
    search: str = "", sort_by: str = "created_at", sort_dir: str = "desc", limit: int = 50, offset: int = 0
) -> dict:
    """Full cross-user audit/brand-check log for the admin panel.
    Search/sort/pagination done in-process (mirrors admin_list_users) -
    email lives in Supabase Auth, not the audits table, so it can't be
    filtered/sorted via a plain PostgREST query anyway. The full 2000-row
    fetch is cached briefly (like admin_list_users) so search/sort/paging
    don't re-fetch it on every interaction."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"audits": [], "total": 0}

    now = datetime.now(timezone.utc)
    if _audits_cache["value"] is not None and _audits_cache["fetched_at"] and now - _audits_cache["fetched_at"] < _LIST_CACHE_TTL:
        audits = [dict(a) for a in _audits_cache["value"]]
    else:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/audits?select=id,user_id,type,domain,name,score,credits_spent,status,created_at&order=created_at.desc&limit=2000",
                    headers=_headers(),
                    timeout=15,
                )
                audits = r.json() if r.status_code == 200 else []
        except Exception as e:
            logger.warning(f"admin_list_audits fetch failed: {e}")
            audits = []

        emails = await _fetch_all_auth_emails()
        for a in audits:
            a["email"] = emails.get(a.get("user_id"), "") if a.get("user_id") else ""

        _audits_cache["value"] = audits
        _audits_cache["fetched_at"] = now
        audits = [dict(a) for a in audits]

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


# ── Bilet (ticket) sistemi ────────────────────────────────────────────────
# Tarama motorunun bulduğu eksiklikleri (şema, entity, içerik vb.) somut,
# token ile satın alınabilen düzeltme işlerine çevirir. Bir bilet: musteri
# satin alir (token dusulur) -> admin bir uzmana atar -> uzman kanit/link ile
# teslim eder -> admin dogrular. Musteriye/uzmana ozel gorunum icin
# ticket_type adi ve alici/uzman e-postasi ayri sorgularla eklenir (ticket'lar
# tablosu FK'lari sadece id tutuyor, e-posta Supabase Auth'ta ayri yasiyor).

async def list_ticket_types(active_only: bool = True) -> list:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            url = f"{SUPABASE_URL}/rest/v1/ticket_types?select=*&order=token_cost.asc"
            if active_only:
                url += "&is_active=eq.true"
            r = await client.get(url, headers=_headers(), timeout=10)
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"list_ticket_types error: {e}")
    return []


async def _get_ticket_type(ticket_type_id: int) -> dict | None:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_types?id=eq.{ticket_type_id}&select=*",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200 and r.json():
                return r.json()[0]
    except Exception as e:
        logger.warning(f"_get_ticket_type error: {e}")
    return None


async def get_ticket_type_by_key(key: str) -> dict | None:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_types?key=eq.{key}&select=*",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200 and r.json():
                return r.json()[0]
    except Exception as e:
        logger.warning(f"get_ticket_type_by_key error: {e}")
    return None


async def mark_ticket_submitted(ticket_id: int) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}",
                headers=_headers(), json={"status": "submitted"}, timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"mark_ticket_submitted error: {e}")
        return False


async def purchase_ticket(user_id: str, ticket_type_id: int, audit_id: str | None = None, target: str = "") -> dict:
    """Deducts token_cost from the buyer's balance and creates the ticket -
    both steps must succeed together, so balance is checked and the profile
    patched before the ticket row is inserted (best-effort atomicity without
    a DB transaction, matching the rest of this file's pattern)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not_configured"}
    ticket_type = await _get_ticket_type(ticket_type_id)
    if not ticket_type or not ticket_type.get("is_active"):
        return {"success": False, "error": "invalid_ticket_type"}
    cost = ticket_type["token_cost"]
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=credit_balance,total_credits_spent",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return {"success": False, "error": "user_not_found"}
            row = r.json()[0]
            balance = row.get("credit_balance", 0)
            if balance < cost:
                return {"success": False, "error": "insufficient_balance"}

            patch_r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(),
                json={"credit_balance": balance - cost, "total_credits_spent": (row.get("total_credits_spent") or 0) + cost},
                timeout=10,
            )
            if patch_r.status_code not in (200, 204):
                return {"success": False, "error": "balance_update_failed"}

            await client.post(
                f"{SUPABASE_URL}/rest/v1/credit_transactions",
                headers=_headers(),
                json={
                    "user_id": user_id, "amount": -cost, "type": "ticket_purchase",
                    "description": f"Bilet satın alma: {ticket_type['name']}",
                },
                timeout=10,
            )

            ticket_r = await client.post(
                f"{SUPABASE_URL}/rest/v1/tickets",
                headers={**_headers(), "Prefer": "return=representation"},
                json={
                    "user_id": user_id, "audit_id": audit_id, "ticket_type_id": ticket_type_id,
                    "target": target or None, "token_cost": cost,
                },
                timeout=10,
            )
            if ticket_r.status_code not in (200, 201) or not ticket_r.json():
                return {"success": False, "error": "ticket_create_failed"}
            new_ticket = ticket_r.json()[0]
            await _clone_ticket_tasks(client, new_ticket["id"], ticket_type_id)
            return {"success": True, "error": None, "ticket_id": new_ticket["id"], "ticket_type_key": ticket_type.get("key")}
    except Exception as e:
        logger.warning(f"purchase_ticket error: {e}")
        return {"success": False, "error": "exception"}


async def _clone_ticket_tasks(client: httpx.AsyncClient, ticket_id: int, ticket_type_id: int) -> None:
    """Copies the standard checklist template onto this specific ticket at
    purchase time - a snapshot, not a live reference, so later edits to the
    template (or a new template version) never change tickets already sold."""
    try:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/ticket_type_tasks?ticket_type_id=eq.{ticket_type_id}&select=title,sort_order,how_to&order=sort_order.asc",
            headers=_headers(), timeout=10,
        )
        templates = r.json() if r.status_code == 200 else []
        if not templates:
            return
        rows = [{"ticket_id": ticket_id, "title": t["title"], "sort_order": t["sort_order"], "how_to": t.get("how_to")} for t in templates]
        await client.post(f"{SUPABASE_URL}/rest/v1/ticket_tasks", headers=_headers(), json=rows, timeout=10)
    except Exception as e:
        logger.warning(f"_clone_ticket_tasks error: {e}")


async def list_ticket_tasks(ticket_id: int) -> list:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_tasks?ticket_id=eq.{ticket_id}&select=*&order=sort_order.asc",
                headers=_headers(), timeout=10,
            )
            return r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"list_ticket_tasks error: {e}")
        return []


async def toggle_ticket_task(task_id: int, ticket_id: int, done: bool) -> bool:
    """ticket_id is required (not just task_id from the URL) so a caller
    with access to ticket A can't toggle a task belonging to ticket B by
    guessing task ids - the endpoint already checked access to ticket_id,
    this scopes the actual write to match."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/ticket_tasks?id=eq.{task_id}&ticket_id=eq.{ticket_id}",
                headers=_headers(),
                json={"is_done": done, "done_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if done else None},
                timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"toggle_ticket_task error: {e}")
        return False


async def _get_unread_ticket_ids(ticket_ids: list, viewer_id: str) -> set:
    if not ticket_ids:
        return set()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/rpc/get_tickets_unread",
                headers=_headers(), json={"p_ticket_ids": ticket_ids, "p_user_id": viewer_id}, timeout=10,
            )
            if r.status_code == 200:
                return {row["ticket_id"] for row in r.json()}
    except Exception as e:
        logger.warning(f"_get_unread_ticket_ids error: {e}")
    return set()


async def mark_ticket_read(ticket_id: int, user_id: str) -> None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/ticket_message_reads",
                headers={**_headers(), "Prefer": "resolution=merge-duplicates"},
                json={"ticket_id": ticket_id, "user_id": user_id, "last_read_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                timeout=10,
            )
    except Exception as e:
        logger.warning(f"mark_ticket_read error: {e}")


async def _enrich_tickets(tickets: list, viewer_id: str = "") -> list:
    """Adds ticket_type_name, user_email, expert_email to each row -
    ticket_type is a simple join, but user/expert emails live in Supabase
    Auth (profiles has no email column), so they're merged in from the
    already-cached _fetch_all_auth_emails(). When viewer_id is given, also
    adds has_unread (a message from someone else, posted after the
    viewer's own last_read_at for that ticket)."""
    if not tickets:
        return tickets
    types = await list_ticket_types(active_only=False)
    type_by_id = {t["id"]: t for t in types}
    emails = await _fetch_all_auth_emails()
    for t in tickets:
        tt = type_by_id.get(t.get("ticket_type_id"), {})
        t["ticket_type_name"] = tt.get("name", "")
        t["ticket_type_key"] = tt.get("key", "")
        t["delivery_template"] = tt.get("delivery_template", "")
        t["user_email"] = emails.get(t.get("user_id"), "")
        t["expert_email"] = emails.get(t.get("assigned_expert_id"), "") if t.get("assigned_expert_id") else ""
    if viewer_id:
        unread_ids = await _get_unread_ticket_ids([t["id"] for t in tickets], viewer_id)
        for t in tickets:
            t["has_unread"] = t["id"] in unread_ids
    return tickets


async def list_user_tickets(user_id: str) -> list:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?user_id=eq.{user_id}&select=*&order=created_at.desc",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                tickets = await _enrich_tickets(r.json(), user_id)
                for t in tickets:
                    t.pop("delivery_template", None)
                return tickets
    except Exception as e:
        logger.warning(f"list_user_tickets error: {e}")
    return []


async def list_expert_tickets(expert_id: str) -> list:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?assigned_expert_id=eq.{expert_id}&select=*&order=created_at.desc",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                return await _enrich_tickets(r.json(), expert_id)
    except Exception as e:
        logger.warning(f"list_expert_tickets error: {e}")
    return []


async def start_ticket_work(ticket_id: int, expert_id: str) -> dict:
    """assigned -> in_progress. Musteri/admin de bu gecisi gorup uzmanin
    ise gercekten basladigini anlar - eskiden bu durum hic kullanilmiyordu."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not_configured"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}&select=assigned_expert_id,status",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return {"success": False, "error": "not_found"}
            row = r.json()[0]
            if row.get("assigned_expert_id") != expert_id:
                return {"success": False, "error": "not_assigned"}
            if row.get("status") != "assigned":
                return {"success": False, "error": "invalid_status"}
            patch_r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}",
                headers=_headers(), json={"status": "in_progress"}, timeout=10,
            )
            return {"success": patch_r.status_code in (200, 204), "error": None}
    except Exception as e:
        logger.warning(f"start_ticket_work error: {e}")
        return {"success": False, "error": "exception"}


async def submit_ticket_evidence(ticket_id: int, expert_id: str, evidence_url: str, evidence_note: str = "") -> dict:
    """Only the expert this ticket is actually assigned to may submit -
    checked here rather than trusted from the request, since the endpoint
    takes the ticket_id from the URL and the expert's identity from their
    own auth token, not from client-supplied data."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not_configured"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}&select=assigned_expert_id,status",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return {"success": False, "error": "not_found"}
            row = r.json()[0]
            if row.get("assigned_expert_id") != expert_id:
                return {"success": False, "error": "not_assigned"}
            if row.get("status") not in ("assigned", "in_progress"):
                return {"success": False, "error": "invalid_status"}

            patch_r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}",
                headers=_headers(),
                json={
                    "status": "submitted", "evidence_url": evidence_url, "evidence_note": evidence_note,
                    "submitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                timeout=10,
            )
            success = patch_r.status_code in (200, 204)
            if success:
                # Kanit ayrica konusma akisina da mesaj olarak eklenir -
                # boylece musteri Biletlerim'i actiginda kaniti gormek icin
                # ayri bir alan/UI'a bakmasi gerekmez, tek yerde gorur.
                await add_ticket_message(
                    ticket_id, expert_id, "expert",
                    body=evidence_note or "İşlem tamamlandı, kanıt eklendi.",
                    attachment_url=evidence_url,
                )
            return {"success": success, "error": None}
    except Exception as e:
        logger.warning(f"submit_ticket_evidence error: {e}")
        return {"success": False, "error": "exception"}


async def admin_list_tickets(status: str = "", admin_id: str = "") -> list:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            url = f"{SUPABASE_URL}/rest/v1/tickets?select=*&order=created_at.desc&limit=500"
            if status:
                url += f"&status=eq.{status}"
            r = await client.get(url, headers=_headers(), timeout=15)
            if r.status_code == 200:
                return await _enrich_tickets(r.json(), admin_id)
    except Exception as e:
        logger.warning(f"admin_list_tickets error: {e}")
    return []


async def admin_assign_ticket(ticket_id: int, expert_id: str) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}",
                headers=_headers(),
                json={
                    "assigned_expert_id": expert_id, "status": "assigned",
                    "assigned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"admin_assign_ticket error: {e}")
    return False


async def _build_delivery_report(ticket_id: int) -> str:
    """Türkçe İş Teslim Raporu - onaylanan bir bilette, checklist'in
    tamamlanma durumu + sureyi kalici bir kayit olarak konusma akisina
    ekler (musteri de gorur, ayri bir alan aramasi gerekmez)."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}&select=*",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return ""
            ticket = r.json()[0]
        types = await list_ticket_types(active_only=False)
        tt = next((t for t in types if t["id"] == ticket.get("ticket_type_id")), {})
        tasks = await list_ticket_tasks(ticket_id)
        emails = await _fetch_all_auth_emails()
        expert_email = emails.get(ticket.get("assigned_expert_id"), "—") if ticket.get("assigned_expert_id") else "—"

        opened = ticket.get("created_at", "")
        closed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            opened_dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
            closed_dt = datetime.now(timezone.utc)
            delta = closed_dt - opened_dt
            hours = delta.total_seconds() / 3600
            duration = f"{delta.days} gün {int(hours % 24)} saat" if delta.days else f"{hours:.1f} saat"
        except Exception:
            duration = "—"

        lines = [
            "## İş Teslim Raporu",
            f"**Hizmet:** {tt.get('name', '—')}",
            f"**Hedef:** {ticket.get('target') or '—'}",
            f"**Açılış:** {opened[:16].replace('T', ' ')}",
            f"**Tamamlanma:** {closed[:16].replace('T', ' ')}",
            f"**Toplam süre:** {duration}",
            f"**Uzman:** {expert_email}",
        ]
        if tasks:
            lines.append("\n**Tamamlanan iş kırılımı:**")
            for tsk in tasks:
                mark = "✓" if tsk.get("is_done") else "—"
                lines.append(f"- [{mark}] {tsk['title']}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"_build_delivery_report error: {e}")
        return ""


async def admin_verify_ticket(ticket_id: int, admin_id: str, approve: bool, reject_reason: str = "") -> bool:
    """Onaylanirsa 'verified'e gecer + Is Teslim Raporu threade eklenir.
    Reddedilirse 'rejected' TERMINAL bir durum degil - 'assigned'a geri
    doner ki uzman gerekcesini gorup duzeltip tekrar teslim edebilsin
    (eskiden reddedilen bir bilet sonsuza kadar kilitli kaliyordu)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            if approve:
                payload = {
                    "status": "verified",
                    "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "verified_by": admin_id,
                }
            else:
                payload = {"status": "assigned", "reject_reason": reject_reason}
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}",
                headers=_headers(), json=payload, timeout=10,
            )
            success = r.status_code in (200, 204)
            if success and approve:
                report = await _build_delivery_report(ticket_id)
                if report:
                    await add_ticket_message(ticket_id, None, "system", body=report)
            elif success and reject_reason:
                await add_ticket_message(ticket_id, admin_id, "admin", body=f"Teslim düzeltme için geri gönderildi:\n{reject_reason}")
            return success
    except Exception as e:
        logger.warning(f"admin_verify_ticket error: {e}")
    return False


async def admin_create_ticket_type(key: str, name: str, description: str, token_cost: int, verification_type: str = "manual") -> dict:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not_configured"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/ticket_types",
                headers=_headers(),
                json={
                    "key": key, "name": name, "description": description,
                    "token_cost": token_cost, "verification_type": verification_type,
                },
                timeout=10,
            )
            if r.status_code in (200, 201):
                return {"success": True, "error": None}
            if r.status_code == 409:
                return {"success": False, "error": "duplicate_key"}
            return {"success": False, "error": f"http_{r.status_code}"}
    except Exception as e:
        logger.warning(f"admin_create_ticket_type error: {e}")
        return {"success": False, "error": "exception"}


async def admin_set_ticket_type_active(ticket_type_id: int, is_active: bool) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/ticket_types?id=eq.{ticket_type_id}",
                headers=_headers(), json={"is_active": is_active}, timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"admin_set_ticket_type_active error: {e}")
    return False


async def admin_set_is_expert(user_id: str, is_expert_flag: bool) -> bool:
    """Grant or revoke the expert panel access for a user."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(), json={"is_expert": is_expert_flag}, timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"admin_set_is_expert error: {e}")
    return False


async def list_experts() -> list:
    """id + email for every is_expert=true profile - powers the admin
    panel's ticket-assignment dropdown."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?select=id,full_name&is_expert=eq.true",
                headers=_headers(), timeout=10,
            )
            experts = r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"list_experts error: {e}")
        return []
    emails = await _fetch_all_auth_emails()
    for e in experts:
        e["email"] = emails.get(e["id"], "")
    return experts


async def get_ticket_role(ticket_id: int, user_id: str) -> tuple[str | None, dict | None]:
    """Returns (role, ticket_row) where role is 'customer' (bought it),
    'expert' (assigned to it), 'admin' (has the tickets scope), or None if
    the caller has no business seeing this ticket at all. is_strict_admin
    and has_admin_scope are checked here rather than trusted from the
    caller, since a ticket's messages can contain real customer/expert
    conversation - access must be verified per-ticket, not just per-role."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None, None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}&select=*",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return None, None
            ticket = r.json()[0]
    except Exception as e:
        logger.warning(f"get_ticket_role error: {e}")
        return None, None

    if ticket.get("user_id") == user_id:
        return "customer", ticket
    if ticket.get("assigned_expert_id") == user_id:
        return "expert", ticket
    if await has_admin_scope(user_id, "tickets"):
        return "admin", ticket
    return None, ticket


async def list_ticket_messages(ticket_id: int) -> list:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_messages?ticket_id=eq.{ticket_id}&select=*&order=created_at.asc",
                headers=_headers(), timeout=10,
            )
            messages = r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"list_ticket_messages error: {e}")
        return []
    emails = await _fetch_all_auth_emails()
    for m in messages:
        m["author_email"] = emails.get(m.get("author_id"), "")
    return messages


async def add_ticket_message(ticket_id: int, author_id: str | None, author_role: str, body: str = "", attachment_url: str = "", attachment_name: str = "") -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    if not body and not attachment_url:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/ticket_messages",
                headers=_headers(),
                json={
                    "ticket_id": ticket_id, "author_id": author_id, "author_role": author_role,
                    "body": body or None, "attachment_url": attachment_url or None, "attachment_name": attachment_name or None,
                },
                timeout=10,
            )
            return r.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"add_ticket_message error: {e}")
    return False


async def create_ticket_upload_url(ticket_id: int, filename: str) -> dict | None:
    """Signed upload URL scoped to this ticket's own folder in the
    ticket-attachments bucket. Returns path+token so the frontend can use
    supabase-js's own storage.from(...).uploadToSignedUrl() rather than us
    guessing the raw HTTP verb/headers Storage expects - our backend never
    handles the file bytes either way. The path is namespaced by ticket_id
    so one ticket's uploads can't collide with or overwrite another's."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._-") or "file"
    path = f"{ticket_id}/{int(datetime.now(timezone.utc).timestamp() * 1000)}_{safe_name}"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/storage/v1/object/upload/sign/ticket-attachments/{path}",
                headers=_headers(), json={"expiresIn": 300}, timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                token = parse_qs(urlparse(data.get("url", "")).query).get("token", [""])[0]
                return {
                    "path": path, "token": token,
                    "public_url": f"{SUPABASE_URL}/storage/v1/object/public/ticket-attachments/{path}",
                }
    except Exception as e:
        logger.warning(f"create_ticket_upload_url error: {e}")
    return None
