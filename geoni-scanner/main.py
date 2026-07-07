"""
GEONI Visibility Scanner MVP - FastAPI Backend
Real Playwright crawler, indexing checks, scoring engine (now with brand
recall as a 6th dimension), multi-dimensional rate limiting, automatic email
report delivery, and a standalone brand-recall-only check for people/brands
without a website (e.g. political candidates, executives).
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr
from typing import Optional, List
import asyncio
import json
import uuid
from datetime import datetime
import logging

from crawler import crawl_domain
from indexing import check_indexing_status
from scoring import compute_ai_visibility_score
from topics import generate_topics_and_opportunities
from ratelimit import enforce_audit_rate_limits, RateLimitExceeded
from mailer import send_audit_report_email
from brand_recall import check_brand_recall, infer_brand_identity
from db import (
    save_audit, save_brand_check, get_user_id_from_token, check_is_premium, get_total_scan_count, deduct_credits,
    is_strict_admin, get_admin_summary, get_admin_scans_daily, get_admin_credits_stats, get_admin_provider_usage,
    admin_list_users, admin_list_audits, admin_adjust_credits, admin_set_is_admin,
    get_manual_balances, set_manual_balance, get_manual_topups_total, list_manual_topups, add_manual_topup,
    get_credit_packages, record_purchase, get_admin_sales_stats, get_pricing_tiers, add_pricing_tier, delete_pricing_tier,
    get_manual_cost, set_manual_cost,
)
from anthropic_admin import get_anthropic_cost_summary
from aws_cost import get_aws_cost_summary
from openai_admin import get_openai_cost_summary
from tavily_admin import get_tavily_usage_summary
from perplexity_admin import get_perplexity_cost_summary
from gemini_admin import get_gemini_cost_summary
from lemonsqueezy import verify_webhook_signature, create_checkout, parse_order_webhook

class AuditRequest(BaseModel):
    domain: str
    email: EmailStr
    competitors: Optional[List[str]] = None
    page_limit: int = 500
    lang: Optional[str] = "tr"
    private: Optional[bool] = False

class AuditResponse(BaseModel):
    job_id: str
    status: str
    estimated_time: int

class BrandCheckRequest(BaseModel):
    type: Optional[str] = "person"   # "person" | "brand"
    name: str
    topic: Optional[str] = ""
    role: Optional[str] = ""
    company: Optional[str] = ""
    sector: Optional[str] = ""
    location: Optional[str] = ""
    linkedin_url: Optional[str] = ""
    website: Optional[str] = ""
    email: Optional[str] = "anonymous@geoni.ai"
    lang: Optional[str] = "tr"
    private: Optional[bool] = False

class BrandCheckResponse(BaseModel):
    job_id: str
    status: str

app = FastAPI(title="GEONI Visibility Scanner MVP", version="0.9.0", description="AI visibility auditing with Playwright crawling, 6-dimension domain scoring, brand recall with rich context, identity verification, and email delivery")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "https://geoni.ai", "https://app.geoni.ai", "https://geoni-frontend.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

jobs_store = {}
brand_checks_store = {}
brand_check_events: dict[str, asyncio.Queue] = {}
audit_events: dict[str, asyncio.Queue] = {}

# Canli SSE ilerleme mesajlari (dil secimine gore, bkz. run_audit_job)
AUDIT_PROGRESS_MESSAGES = {
    "tr": {
        "crawling":      "{domain} taranıyor…",
        "pages_scanned": "{count} sayfa tarandı ✓",
        "checking_bots": "AI botlarının erişimi kontrol ediliyor…",
        "index_checked": "Dizin durumu kontrol edildi ✓",
        "scoring":       "Skor hesaplanıyor…",
    },
    "en": {
        "crawling":      "Scanning {domain}…",
        "pages_scanned": "{count} pages scanned ✓",
        "checking_bots": "Checking AI bot access…",
        "index_checked": "Index status checked ✓",
        "scoring":       "Calculating score…",
    },
}


def _rate_limit_message(lang: str, seconds: int) -> str:
    if lang == "en":
        return f"Too many requests. Please try again in {seconds} seconds."
    return f"Çok fazla istek gönderdiniz. Lütfen {seconds} saniye sonra tekrar deneyin."


def _login_required_message(lang: str) -> str:
    if lang == "en":
        return "Please sign in to run a person/brand check."
    return "Kişi/marka taraması için lütfen giriş yapın."


def get_client_ip(request: Request) -> str:
    """
    Resolve the real client IP, accounting for the ALB which sits in front
    of this service. ALB appends the original client IP as the first entry
    in X-Forwarded-For; fall back to request.client.host for local/dev runs.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def run_audit_job(job_id: str, request: AuditRequest, token: str = ''):
    queue = audit_events.get(job_id)
    msgs = AUDIT_PROGRESS_MESSAGES.get(request.lang, AUDIT_PROGRESS_MESSAGES["tr"])

    def emit(message: str):
        if queue is not None:
            queue.put_nowait(message)

    try:
        jobs_store[job_id]["status"] = "crawling"
        emit(msgs["crawling"].format(domain=request.domain))
        crawl_result = await crawl_domain(request.domain, request.page_limit)
        emit(msgs["pages_scanned"].format(count=crawl_result['total_pages']))

        jobs_store[job_id]["status"] = "indexing"
        emit(msgs["checking_bots"])
        indexing_status = await check_indexing_status(crawl_result["pages"])
        emit(msgs["index_checked"])

        jobs_store[job_id]["status"] = "scoring"

        # Infer brand name + topic from crawled page titles, then check
        # whether the LLM's trained knowledge already recognizes this brand
        # within that topic. This becomes a 6th scoring dimension.
        page_titles = [p.get("title", "") for p in crawl_result.get("pages", []) if p.get("title")]
        identity = await infer_brand_identity(request.domain, page_titles)
        brand_recall_result = await check_brand_recall(identity["name"], identity["topic"], on_progress=emit, lang=request.lang)
        emit(msgs["scoring"])

        score_result = await compute_ai_visibility_score(crawl_result, indexing_status, brand_recall_result)

        topics = await generate_topics_and_opportunities(request.domain, crawl_result["pages"])

        result_payload = {
            "domain": request.domain,
            "score": score_result["overall_score"],
            "score_breakdown": score_result["breakdown"],
            "scoring_version": score_result.get("scoring_version"),
            "weights_used": score_result.get("weights_used"),
            "diagnostics": score_result.get("diagnostics"),
            "total_pages": crawl_result["total_pages"],
            "indexed_pages": indexing_status["indexed_count"],
            "platforms": {
                # Not: bu alanlar artik ARAMA/ALINTILANMA botlarina (OAI-SearchBot,
                # ChatGPT-User, Claude-SearchBot, PerplexityBot...) gore hesaplaniyor,
                # yalnizca egitim crawler'larina (GPTBot vb.) gore degil (Madde 2.5).
                "chatgpt": indexing_status.get("openai", False),
                "anthropic": indexing_status.get("anthropic", False),
                "perplexity": indexing_status.get("perplexity", False),
                "google": indexing_status.get("google", 0),
            },
            "bot_access": indexing_status.get("bot_access", {}),
            "llms_txt": indexing_status.get("llms_txt", False),
            "top_topics": topics["performing_topics"],
            "opportunities": topics["opportunity_topics"],
            "brand_recall": {
                "checked": brand_recall_result.get("checked", False),
                "recognized": brand_recall_result.get("recognized", False),
                "score": brand_recall_result.get("score"),
                "score_legacy": brand_recall_result.get("score_legacy"),
                "scoring_version": brand_recall_result.get("scoring_version"),
                "inferred_name": identity["name"],
                "inferred_topic": identity["topic"],
            },
            "created_at": datetime.now().isoformat()
        }

        jobs_store[job_id].update({
            "status": "complete",
            "result": result_payload,
            "completed_at": datetime.now().isoformat()
        })

        # Ozel/gecici tarama: Dashboard/Tarama Gecmisi'nde hic gorunmesin diye
        # audits tablosuna hicbir kayit yazilmaz. Gercek AI sorgu maliyeti
        # aynen olustugu icin kontor yine de dusulur (suistimali onlemek icin).
        user_id = await get_user_id_from_token(token) if token else None
        if request.private:
            if user_id:
                await deduct_credits(user_id, 5, "web_audit_private", job_id)
            logger.info(f"Private audit job {job_id} completed, not saved")
        else:
            await save_audit(job_id, {"domain": request.domain, "email": request.email}, jobs_store[job_id]["result"], user_id)
            logger.info(f"Audit job {job_id} completed successfully")

        # Fire-and-forget email delivery. send_audit_report_email never raises,
        # so a failed/unconfigured email send cannot affect the audit's success.
        email_sent = await send_audit_report_email(request.email, request.domain, result_payload, lang=request.lang or "tr")
        jobs_store[job_id]["email_sent"] = email_sent

    except Exception as e:
        logger.error(f"Audit job {job_id} failed: {str(e)}")
        jobs_store[job_id]["status"] = "failed"
        jobs_store[job_id]["error"] = str(e)
    finally:
        emit("__done__")


async def run_brand_check_job(job_id: str, request: BrandCheckRequest, token: str = ''):
    """
    Standalone brand-recall-only check for people/brands without a website
    (e.g. political candidates, executives, personal brands). No crawling —
    just the same knowledge-recall query used by the geoni.ai widget, kept
    consistent so results match whether run there or here.
    """
    queue = brand_check_events.get(job_id)

    def emit(message: str):
        if queue is not None:
            queue.put_nowait(message)

    try:
        result = await check_brand_recall(
            name=request.name,
            topic=request.topic or "",
            email=request.email or "",
            role=request.role or "",
            company=request.company or "",
            sector=request.sector or "",
            location=request.location or "",
            linkedin_url=request.linkedin_url or "",
            website=request.website or "",
            entity_type=request.type or "person",
            on_progress=emit,
            lang=request.lang or "tr",
        )
        brand_checks_store[job_id].update({
            "status": "complete",
            "result": {
                "name": request.name,
                "topic": request.topic,
                "recognized": result.get("recognized", False),
                "recognition_count": result.get("recognition_count", 0),
                "score": result.get("score", 0),
                "score_legacy": result.get("score_legacy"),
                "scoring_version": result.get("scoring_version"),
                "score_breakdown": result.get("score_breakdown", {}),
                "model_results": result.get("model_results", {}),
                "google_result_count": result.get("google_result_count", 0),
                "performing_topics": result.get("performing_topics", []),
                "opportunity_topics": result.get("opportunity_topics", []),
                "checked": result.get("checked", False),
                "raw_list": result.get("raw_list"),
                "created_at": datetime.now().isoformat(),
            },
            "completed_at": datetime.now().isoformat(),
        })
        # Ozel/gecici tarama: Dashboard/Tarama Gecmisi'nde hic gorunmesin diye
        # audits tablosuna hicbir kayit yazilmaz. Gercek AI sorgu maliyeti
        # aynen olustugu icin kontor yine de dusulur (suistimali onlemek icin).
        user_id = await get_user_id_from_token(token) if token else None
        if request.private:
            if user_id:
                await deduct_credits(user_id, 10, f"{request.type or 'person'}_check_private", job_id)
            logger.info(f"Private brand check job {job_id} completed for '{request.name}', not saved")
        else:
            await save_brand_check(job_id, request.__dict__, brand_checks_store[job_id]["result"], user_id)
            logger.info(f"Brand check job {job_id} completed for '{request.name}'"  )
    except Exception as e:
        logger.error(f"Brand check job {job_id} failed: {str(e)}")
        brand_checks_store[job_id]["status"] = "failed"
        brand_checks_store[job_id]["error"] = str(e)
    finally:
        emit("__done__")


@app.get("/")
@app.get("/health")
async def health():
    return {"status": "healthy", "version": "0.9.0", "timestamp": datetime.now().isoformat()}

@app.get("/api/stats/scan-count")
async def scan_count():
    """Public aggregate count for the landing page social-proof counter."""
    return {"count": await get_total_scan_count()}

@app.post("/api/audit/quick", response_model=AuditResponse)
async def start_audit(request: AuditRequest, background_tasks: BackgroundTasks, http_request: Request):
    client_ip = get_client_ip(http_request)

    try:
        # Skip rate limit for premium/admin users
        auth_header_rl = http_request.headers.get("Authorization", "")
        token_rl = auth_header_rl.replace("Bearer ", "") if auth_header_rl.startswith("Bearer ") else ""
        user_id_rl = await get_user_id_from_token(token_rl) if token_rl else None
        is_premium = await check_is_premium(user_id_rl) if user_id_rl else False
        if not is_premium:
            enforce_audit_rate_limits(client_ip, request.email, request.domain)
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429,
            detail=_rate_limit_message(request.lang or "tr", e.retry_after_seconds),
            headers={"Retry-After": str(e.retry_after_seconds)},
        )

    job_id = str(uuid.uuid4())
    jobs_store[job_id] = {"job_id": job_id, "status": "queued", "domain": request.domain, "email": request.email, "created_at": datetime.now().isoformat(), "result": None, "error": None}
    audit_events[job_id] = asyncio.Queue()
    # Extract user_id from Authorization header if present
    auth_header = http_request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
    background_tasks.add_task(run_audit_job, job_id, request, token)
    logger.info(f"Audit job {job_id} created for {request.domain} (ip={client_ip})")
    return AuditResponse(job_id=job_id, status="queued", estimated_time=300)

@app.get("/api/audit/{job_id}")
async def get_audit_status(job_id: str):
    if job_id not in jobs_store:
        raise HTTPException(status_code=404, detail="Audit job not found")
    job = jobs_store[job_id]
    if job["status"] == "complete":
        return {"job_id": job_id, "status": "complete", "result": job["result"], "email_sent": job.get("email_sent", False)}
    elif job["status"] == "failed":
        raise HTTPException(status_code=500, detail=f"Audit failed: {job['error']}")
    else:
        return {"job_id": job_id, "status": job["status"], "created_at": job["created_at"]}

@app.get("/api/audit/{job_id}/stream")
async def stream_audit(job_id: str):
    """Live crawl/index/model progress for the loading screen (SSE)."""
    queue = audit_events.get(job_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="Audit job not found")

    async def event_generator():
        try:
            while True:
                message = await queue.get()
                if message == "__done__":
                    job = jobs_store.get(job_id, {})
                    yield f"data: {json.dumps({'done': True, 'status': job.get('status', 'complete')})}\n\n"
                    break
                yield f"data: {json.dumps({'message': message})}\n\n"
        finally:
            audit_events.pop(job_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@app.get("/api/audit/{job_id}/results")
async def get_audit_results(job_id: str):
    return await get_audit_status(job_id)

@app.post("/api/brand-check", response_model=BrandCheckResponse)
async def start_brand_check(request: BrandCheckRequest, background_tasks: BackgroundTasks, http_request: Request):
    """
    Standalone name/topic recall check — no domain required. For people or
    brands without a website (political candidates, executives, personal
    brands) who want to know if AI already recognizes them in their field.
    """
    client_ip = get_client_ip(http_request)

    # Kisi/marka taramasi 4 AI motoruna paralel gercek sorgu maliyeti tasir -
    # web sitesi taramasinin aksine anonim kullanima acik degil, giris sart.
    auth_header_rl2 = http_request.headers.get("Authorization", "")
    token_rl2 = auth_header_rl2.replace("Bearer ", "") if auth_header_rl2.startswith("Bearer ") else ""
    user_id_rl2 = await get_user_id_from_token(token_rl2) if token_rl2 else None
    if not user_id_rl2:
        raise HTTPException(status_code=401, detail=_login_required_message(request.lang or "tr"))

    try:
        # Skip rate limit for premium/admin users
        is_premium2 = await check_is_premium(user_id_rl2)
        if not is_premium2:
            enforce_audit_rate_limits(client_ip, request.email, request.name)
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429,
            detail=_rate_limit_message(request.lang or "tr", e.retry_after_seconds),
            headers={"Retry-After": str(e.retry_after_seconds)},
        )

    job_id = str(uuid.uuid4())
    brand_checks_store[job_id] = {"job_id": job_id, "status": "queued", "name": request.name, "topic": request.topic, "created_at": datetime.now().isoformat(), "result": None, "error": None}
    brand_check_events[job_id] = asyncio.Queue()
    auth_header = http_request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
    background_tasks.add_task(run_brand_check_job, job_id, request, token)
    logger.info(f"Brand check job {job_id} created for '{request.name}' (ip={client_ip})")
    return BrandCheckResponse(job_id=job_id, status="queued")

@app.get("/api/brand-check/{job_id}")
async def get_brand_check_status(job_id: str):
    if job_id not in brand_checks_store:
        raise HTTPException(status_code=404, detail="Brand check job not found")
    job = brand_checks_store[job_id]
    if job["status"] == "complete":
        return {"job_id": job_id, "status": "complete", "result": job["result"]}
    elif job["status"] == "failed":
        raise HTTPException(status_code=500, detail=f"Brand check failed: {job['error']}")
    else:
        return {"job_id": job_id, "status": job["status"], "created_at": job["created_at"]}

@app.get("/api/brand-check/{job_id}/stream")
async def stream_brand_check(job_id: str):
    """
    Live per-model progress for the loading screen (SSE). The queue is
    created alongside the job in start_brand_check, so events emitted
    before this connects are buffered, not lost.
    """
    queue = brand_check_events.get(job_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="Brand check job not found")

    async def event_generator():
        try:
            while True:
                message = await queue.get()
                if message == "__done__":
                    job = brand_checks_store.get(job_id, {})
                    yield f"data: {json.dumps({'done': True, 'status': job.get('status', 'complete')})}\n\n"
                    break
                yield f"data: {json.dumps({'message': message})}\n\n"
        finally:
            brand_check_events.pop(job_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@app.get("/api/score/{domain}")
async def get_cached_score(domain: str):
    return {"domain": domain, "score": None, "note": "Caching not yet implemented"}

# ── Admin panel ─────────────────────────────────────────────────────────
# Tum admin endpoint'leri Authorization: Bearer <supabase_token> bekler ve
# profiles.is_admin=true zorunlu kilar (check_is_premium'un aksine, kredi
# satin alan ama admin olmayan kullanicilari GECIRMEZ).

class CreditAdjustRequest(BaseModel):
    delta: int
    reason: Optional[str] = ""

class AdminFlagRequest(BaseModel):
    is_admin: bool

async def _require_admin(http_request: Request) -> str:
    auth_header = http_request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_id = await get_user_id_from_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    if not await is_strict_admin(user_id):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user_id

@app.get("/api/admin/stats/summary")
async def admin_stats_summary(http_request: Request):
    await _require_admin(http_request)
    return await get_admin_summary()

@app.get("/api/admin/stats/scans-daily")
async def admin_stats_scans_daily(http_request: Request, days: int = 14):
    await _require_admin(http_request)
    return await get_admin_scans_daily(days=days)

@app.get("/api/admin/stats/credits")
async def admin_stats_credits(http_request: Request, days: int = 14):
    await _require_admin(http_request)
    return await get_admin_credits_stats(days=days)

@app.get("/api/admin/stats/provider-usage")
async def admin_stats_provider_usage(http_request: Request):
    await _require_admin(http_request)
    return await get_admin_provider_usage()

@app.get("/api/admin/stats/anthropic-cost")
async def admin_stats_anthropic_cost(http_request: Request):
    await _require_admin(http_request)
    return await get_anthropic_cost_summary() or {}

@app.get("/api/admin/stats/aws-cost")
async def admin_stats_aws_cost(http_request: Request):
    await _require_admin(http_request)
    return await asyncio.to_thread(get_aws_cost_summary) or {}

class ManualBalanceRequest(BaseModel):
    provider: str
    balance: float
    currency: Optional[str] = "USD"

@app.get("/api/admin/stats/manual-balances")
async def admin_manual_balances(http_request: Request):
    await _require_admin(http_request)
    return await get_manual_balances()

@app.post("/api/admin/stats/manual-balances")
async def admin_set_manual_balance(body: ManualBalanceRequest, http_request: Request):
    await _require_admin(http_request)
    if not await set_manual_balance(body.provider, body.balance, body.currency):
        raise HTTPException(status_code=400, detail="Balance update failed")
    return {"success": True}

@app.get("/api/admin/stats/openai-cost")
async def admin_stats_openai_cost(http_request: Request):
    await _require_admin(http_request)
    return await get_openai_cost_summary() or {}

@app.get("/api/admin/stats/tavily-usage")
async def admin_stats_tavily_usage(http_request: Request):
    await _require_admin(http_request)
    return await get_tavily_usage_summary()

@app.get("/api/admin/stats/perplexity-cost")
async def admin_stats_perplexity_cost(http_request: Request):
    await _require_admin(http_request)
    return await get_perplexity_cost_summary()

@app.get("/api/admin/stats/gemini-cost")
async def admin_stats_gemini_cost(http_request: Request):
    await _require_admin(http_request)
    return await get_gemini_cost_summary() or {}

class TopupRequest(BaseModel):
    provider: str
    amount: float
    note: Optional[str] = ""

@app.get("/api/admin/stats/topups")
async def admin_topups(http_request: Request, provider: str):
    await _require_admin(http_request)
    total = await get_manual_topups_total(provider)
    history = await list_manual_topups(provider)
    return {"total": total, "history": history}

@app.post("/api/admin/stats/topups")
async def admin_add_topup(body: TopupRequest, http_request: Request):
    await _require_admin(http_request)
    if not await add_manual_topup(body.provider, body.amount, body.note):
        raise HTTPException(status_code=400, detail="Top-up kaydedilemedi")
    return {"success": True}

class ManualCostRequest(BaseModel):
    provider: str
    current_cost: float
    projected_cost: Optional[float] = None
    cycle_start: Optional[str] = None
    cycle_end: Optional[str] = None
    note: Optional[str] = ""

@app.get("/api/admin/stats/manual-cost")
async def admin_manual_cost(http_request: Request, provider: str):
    await _require_admin(http_request)
    return await get_manual_cost(provider) or {}

@app.post("/api/admin/stats/manual-cost")
async def admin_set_manual_cost(body: ManualCostRequest, http_request: Request):
    await _require_admin(http_request)
    ok = await set_manual_cost(
        body.provider, body.current_cost, body.projected_cost,
        body.cycle_start, body.cycle_end, body.note,
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Maliyet kaydedilemedi")
    return {"success": True}

@app.get("/api/admin/stats/sales")
async def admin_sales_stats(http_request: Request, days: int = 14):
    await _require_admin(http_request)
    return await get_admin_sales_stats(days=days)

class PricingTierRequest(BaseModel):
    platform: str = "web"
    min_credits: int
    max_credits: Optional[int] = None
    price_per_credit: float
    currency: str = "TRY"

@app.get("/api/admin/pricing-tiers")
async def admin_get_pricing_tiers(http_request: Request):
    await _require_admin(http_request)
    return await get_pricing_tiers()

@app.post("/api/admin/pricing-tiers")
async def admin_add_pricing_tier(body: PricingTierRequest, http_request: Request):
    await _require_admin(http_request)
    if not await add_pricing_tier(body.platform, body.min_credits, body.max_credits, body.price_per_credit, body.currency):
        raise HTTPException(status_code=400, detail="Fiyat kademesi eklenemedi")
    return {"success": True}

@app.delete("/api/admin/pricing-tiers/{tier_id}")
async def admin_delete_pricing_tier(tier_id: str, http_request: Request):
    await _require_admin(http_request)
    if not await delete_pricing_tier(tier_id):
        raise HTTPException(status_code=400, detail="Fiyat kademesi silinemedi")
    return {"success": True}

@app.get("/api/credit-packages")
async def credit_packages():
    return await get_credit_packages(active_only=True)

class CheckoutRequest(BaseModel):
    package_id: str

@app.post("/api/checkout/create")
async def create_checkout_session(body: CheckoutRequest, http_request: Request):
    auth_header = http_request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
    user_id = await get_user_id_from_token(token) if token else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Giriş yapmanız gerekiyor")

    packages = await get_credit_packages(active_only=True)
    package = next((p for p in packages if p["id"] == body.package_id), None)
    if not package or not package.get("lemonsqueezy_variant_id"):
        raise HTTPException(status_code=400, detail="Geçersiz paket")

    url = await create_checkout(package["lemonsqueezy_variant_id"], user_id, package["credits"])
    if not url:
        raise HTTPException(status_code=502, detail="Ödeme sayfası oluşturulamadı")
    return {"checkout_url": url}

@app.post("/api/webhooks/lemonsqueezy")
async def lemonsqueezy_webhook(http_request: Request):
    raw_body = await http_request.body()
    signature = http_request.headers.get("X-Signature", "")
    if not verify_webhook_signature(raw_body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(raw_body)
    order = parse_order_webhook(payload)
    if not order:
        return {"ignored": True}

    ok = await record_purchase(
        user_id=order["user_id"],
        credits=order["credits"],
        amount_paid=order["amount_paid"],
        currency_paid=order["currency_paid"],
        external_id=order["external_id"],
    )
    return {"success": ok}

@app.get("/api/admin/users")
async def admin_users(http_request: Request, search: str = "", limit: int = 50, offset: int = 0):
    await _require_admin(http_request)
    return await admin_list_users(search=search, limit=limit, offset=offset)

@app.post("/api/admin/users/{user_id}/credits")
async def admin_users_credits(user_id: str, body: CreditAdjustRequest, http_request: Request):
    await _require_admin(http_request)
    if not await admin_adjust_credits(user_id, body.delta, body.reason):
        raise HTTPException(status_code=400, detail="Credit adjustment failed")
    return {"success": True}

@app.post("/api/admin/users/{user_id}/admin-flag")
async def admin_users_flag(user_id: str, body: AdminFlagRequest, http_request: Request):
    await _require_admin(http_request)
    if not await admin_set_is_admin(user_id, body.is_admin):
        raise HTTPException(status_code=400, detail="Update failed")
    return {"success": True}

@app.get("/api/admin/audits")
async def admin_audits(http_request: Request, limit: int = 50, offset: int = 0):
    await _require_admin(http_request)
    return await admin_list_audits(limit=limit, offset=offset)

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {str(exc)}")
    return {"error": "Internal server error"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
