"""
GEONI - Supabase database integration
Saves audit results and brand check results to Supabase.
Uses service role key to bypass RLS.
"""

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
                    await deduct_credits(user_id, 10, "web_audit", job_id)
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
    credits = 5

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
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=credit_balance",
                headers=_headers(),
                timeout=10,
            )
            if r.status_code != 200:
                return False
            data = r.json()
            if not data:
                return False
            current_balance = data[0].get("credit_balance", 0)
            if current_balance < amount:
                logger.warning(f"Insufficient credits for user {user_id}: {current_balance} < {amount}")
                return False

            # Update balance
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(),
                json={
                    "credit_balance": current_balance - amount,
                    "total_credits_spent": current_balance,  # will be updated by DB trigger ideally
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


async def get_admin_overview() -> dict:
    """Aggregate stats for the admin panel overview tab."""
    empty = {
        "total_users": 0, "total_audits": 0, "audits_today": 0, "audits_week": 0,
        "credits_purchased": 0, "credits_spent": 0, "provider_usage": {"today": {}, "week": {}},
    }
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return empty

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_start = (now - timedelta(days=7)).isoformat()

    async def count(query: str) -> int:
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

    total_users = await count("profiles?select=id")
    total_audits = await count("audits?select=id")
    audits_today = await count(f"audits?select=id&created_at=gte.{today_start}")
    audits_week = await count(f"audits?select=id&created_at=gte.{week_start}")

    credits_purchased = 0
    credits_spent = 0
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?select=total_credits_purchased,total_credits_spent",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                for row in r.json():
                    credits_purchased += row.get("total_credits_purchased") or 0
                    credits_spent += row.get("total_credits_spent") or 0
    except Exception as e:
        logger.warning(f"Credit aggregate failed: {e}")

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

    return {
        "total_users": total_users,
        "total_audits": total_audits,
        "audits_today": audits_today,
        "audits_week": audits_week,
        "credits_purchased": credits_purchased,
        "credits_spent": credits_spent,
        "provider_usage": provider_usage,
    }


async def admin_list_users(search: str = "", limit: int = 50, offset: int = 0) -> dict:
    """Merges profiles with auth emails (profiles has no email column). Search/pagination done in-process - fine at MVP scale."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"users": [], "total": 0}

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?select=id,full_name,credit_balance,total_credits_purchased,total_credits_spent,is_admin,created_at&order=created_at.desc&limit=1000",
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
    """Manual credit grant (positive delta) or deduction (negative delta) by an admin."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not delta:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=credit_balance,total_credits_purchased",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return False
            row = r.json()[0]
            new_balance = max(0, row.get("credit_balance", 0) + delta)
            update = {"credit_balance": new_balance}
            if delta > 0:
                update["total_credits_purchased"] = (row.get("total_credits_purchased") or 0) + delta

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


async def admin_list_audits(limit: int = 50, offset: int = 0) -> dict:
    """Full cross-user audit/brand-check log for the admin panel."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"audits": [], "total": 0}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits?select=id,user_id,type,domain,name,score,credits_spent,status,created_at&order=created_at.desc&limit={limit}&offset={offset}",
                headers={**_headers(), "Prefer": "count=exact"},
                timeout=15,
            )
            audits = r.json() if r.status_code == 200 else []
            cr = r.headers.get("content-range", "")
            total_s = cr.split("/")[-1] if "/" in cr else ""
            total = int(total_s) if total_s.isdigit() else len(audits)

        emails = await _fetch_all_auth_emails()
        for a in audits:
            a["email"] = emails.get(a.get("user_id"), "") if a.get("user_id") else ""
        return {"audits": audits, "total": total}
    except Exception as e:
        logger.warning(f"admin_list_audits error: {e}")
        return {"audits": [], "total": 0}
