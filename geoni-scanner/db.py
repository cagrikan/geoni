"""
GEONI - Supabase database integration
Saves audit results and brand check results to Supabase.
Uses service role key to bypass RLS.
"""

import os
import logging
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
