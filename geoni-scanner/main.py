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
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, List
import asyncio
import os
import secrets
import json
import uuid
from datetime import datetime
import logging

from crawler import crawl_domain
from indexing import check_indexing_status
from scoring import compute_ai_visibility_score
from topics import generate_topics_and_opportunities
from ratelimit import enforce_audit_rate_limits, RateLimitExceeded
from mailer import send_audit_report_email, send_purchase_email, send_refund_email
from brand_recall import check_brand_recall, infer_brand_identity
from db import (
    save_audit, save_brand_check, get_user_id_from_token, check_is_premium, get_total_scan_count, deduct_credits,
    is_strict_admin, get_admin_summary, get_admin_scans_daily, get_admin_credits_stats, get_admin_provider_usage,
    admin_list_users, admin_list_audits, admin_get_audit, admin_adjust_credits, admin_set_is_admin,
    get_manual_balances, set_manual_balance, get_manual_topups_total, list_manual_topups, add_manual_topup,
    get_credit_packages, record_purchase, get_admin_sales_stats, get_pricing_tiers, add_pricing_tier, delete_pricing_tier,
    get_credit_transaction, transaction_exists, record_refund,
    get_manual_cost, set_manual_cost, list_campaigns, create_campaign, delete_campaign,
    is_expert, list_ticket_types, purchase_ticket, list_user_tickets, list_expert_tickets,
    submit_ticket_evidence, start_ticket_work, admin_list_tickets, admin_assign_ticket, admin_verify_ticket,
    admin_create_ticket_type, admin_set_ticket_type_active, admin_set_is_expert, list_experts,
    has_admin_scope, is_user_suspended, admin_get_user_detail,
    admin_get_user_audits, admin_get_user_transactions, admin_get_user_tickets, admin_set_user_notes,
    admin_set_suspended, admin_set_admin_scopes,
    get_ticket_role, list_ticket_messages, add_ticket_message, create_ticket_upload_url, mark_ticket_read, notify_ticket_event,
    list_ticket_tasks, toggle_ticket_task, dispute_ticket,
)
from anthropic_admin import get_anthropic_cost_summary
from aws_cost import get_aws_cost_summary
from openai_admin import get_openai_cost_summary
from tavily_admin import get_tavily_usage_summary
from perplexity_admin import get_perplexity_cost_summary
from gemini_admin import get_gemini_cost_summary
from total_cost_admin import get_admin_total_cost_summary
from lemonsqueezy import verify_webhook_signature, create_checkout, parse_order_webhook
import polar
from ticket_automation import fulfill_llms_robots_ticket

class AuditRequest(BaseModel):
    domain: str
    email: EmailStr
    competitors: Optional[List[str]] = None
    page_limit: int = 500
    lang: Optional[str] = "tr"
    private: Optional[bool] = False
    custom_queries: Optional[List[str]] = None  # kullanici tanimli SOV sorgulari

    @field_validator("email")
    @classmethod
    def _reject_non_ascii_email(cls, v: str) -> str:
        # EmailStr, SMTPUTF8 sayesinde emoji/unicode yerel kisimlari kabul
        # ediyor (or. 🎉@x.com). Bunlar pratikte teslim edilemez ama gecerli
        # sayilip bosuna tarama + LLM maliyeti tetikliyor - ASCII sarti koy.
        if not v.isascii():
            raise ValueError("value is not a valid email address: contains non-ASCII characters")
        return v

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
    custom_queries: Optional[List[str]] = None  # kullanici tanimli SOV sorgulari

class BrandCheckResponse(BaseModel):
    job_id: str
    status: str

from monitor import monitor_loop
from stability import build_stability
from scanqueue import acquire_scan_slot, release_scan_slot, estimate_wait_seconds

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

@app.on_event("startup")
async def _start_monitor():
    # Izleme v2: haftalik otomatik yeniden tarama dongusu (bkz. monitor.py)
    asyncio.create_task(monitor_loop())


jobs_store = {}
brand_checks_store = {}
brand_check_events: dict[str, asyncio.Queue] = {}
audit_events: dict[str, asyncio.Queue] = {}

# Canli SSE ilerleme mesajlari (dil secimine gore, bkz. run_audit_job)
AUDIT_PROGRESS_MESSAGES = {
    "tr": {
        "queue_wait":    "Sorgunuz AI motorlarına iletildi — yanıtları bekleniyor (tahmini ~{mins} dk)…",
        "crawling":      "{domain} taranıyor…",
        "pages_scanned": "{count} sayfa tarandı ✓",
        "checking_bots": "AI botlarının erişimi kontrol ediliyor…",
        "index_checked": "Dizin durumu kontrol edildi ✓",
        "scoring":       "Skor hesaplanıyor…",
    },
    "en": {
        "queue_wait":    "Your query was sent to the AI engines — waiting for their responses (est. ~{mins} min)…",
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


def _suspended_message(lang: str) -> str:
    if lang == "en":
        return "Your account has been suspended. Please contact support."
    return "Hesabınız askıya alınmış. Lütfen destek ile iletişime geçin."


def get_client_ip(request: Request) -> str:
    """
    Resolve the real client IP, accounting for the ALB which sits in front
    of this service (no CloudFront in the chain).

    AWS ALB APPENDS the real client IP to the END of X-Forwarded-For. The
    LAST entry is therefore the only trustworthy one: any values to its left
    can be spoofed by the client (e.g. sending "X-Forwarded-For: 1.2.3.4"
    via Postman), so reading the FIRST entry would let an attacker rotate a
    fake IP on every request and defeat the per-IP rate limit. Read the last
    entry; fall back to request.client.host for local/dev runs.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"


async def run_audit_job(job_id: str, request: AuditRequest, token: str = ''):
    queue = audit_events.get(job_id)
    msgs = AUDIT_PROGRESS_MESSAGES.get(request.lang, AUDIT_PROGRESS_MESSAGES["tr"])

    def emit(message: str):
        if queue is not None:
            queue.put_nowait(message)

    slot_acquired = False
    try:
        # Tarama kuyrugu: ayni anda en cok SCAN_CONCURRENCY tarama (bkz. scanqueue.py).
        # Slot doluysa kullaniciya tahmini bekleme suresi soylenir; release
        # asagidaki finally'de — pipeline nasil biterse bitsin slot birakilir.
        wait_s = estimate_wait_seconds()
        if wait_s > 0:
            emit(msgs["queue_wait"].format(mins=max(1, round(wait_s / 60))))
        await acquire_scan_slot()
        slot_acquired = True
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
        brand_recall_result = await check_brand_recall(identity["name"], identity["topic"], on_progress=emit, lang=request.lang, custom_queries=request.custom_queries, website=request.domain)
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
            # llms.txt otomasyonu (bilet sistemi) icin sayfa ozeti - sadece
            # baslik+aciklamasi olan sayfalar, ilk 20 - ham crawl_result'un
            # tamami degil, uzun taramalarda result_json'u sismesin diye.
            "pages": [
                {"url": p.get("url"), "title": p.get("title"), "meta_description": p.get("meta_description")}
                for p in crawl_result.get("pages", []) if p.get("title")
            ][:20],
            "brand_recall": {
                "checked": brand_recall_result.get("checked", False),
                "recognized": brand_recall_result.get("recognized", False),
                "score": brand_recall_result.get("score"),
                "score_legacy": brand_recall_result.get("score_legacy"),
                "scoring_version": brand_recall_result.get("scoring_version"),
                "inferred_name": identity["name"],
                "inferred_topic": identity["topic"],
            },
            # v3: Share of Voice — markayi bilmeyen kullanicinin kategori
            # sorgularinda gorunurluk + ayni yanitlardan cikarilan rakipler.
            "sov": brand_recall_result.get("sov"),
            # Skor istikrari: yumusatilmis skor + degisim kaynagi (oynaklik notu)
            "stability": await build_stability("web", request.domain,
                                               score_result["overall_score"],
                                               score_result["breakdown"],
                                               score_result.get("weights_used")),
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
        if slot_acquired:
            release_scan_slot()
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

    slot_acquired2 = False
    try:
        # Ayni tarama kuyrugu (bkz. scanqueue.py) — kisi taramasi da LLM yogun
        wait_s2 = estimate_wait_seconds()
        if wait_s2 > 0:
            lang2 = (request.lang or "tr")
            emit(AUDIT_PROGRESS_MESSAGES.get(lang2, AUDIT_PROGRESS_MESSAGES["tr"])["queue_wait"].format(mins=max(1, round(wait_s2 / 60))))
        await acquire_scan_slot()
        slot_acquired2 = True
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
            custom_queries=request.custom_queries,
        )
        brand_checks_store[job_id].update({
            "status": "complete",
            "result": {
                "name": request.name,
                "topic": request.topic,
                # Kimlik uyusmazliginda arayuz aciklamali ekrani gosterebilsin -
                # eskiden bu bayrak kayda yazilmiyor, kullanici ici bos bir
                # "0 raporu" goruyordu.
                "identity_mismatch": result.get("identity_mismatch", False),
                "match_score": result.get("match_score"),
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
                "sov": result.get("sov"),
                "stability": await build_stability(request.type or "person", request.name,
                                                   result.get("score"),
                                                   result.get("score_breakdown", {})),
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
        if slot_acquired2:
            release_scan_slot()
        emit("__done__")


@app.get("/")
@app.get("/health")
async def health():
    return {"status": "healthy", "version": "0.9.0", "timestamp": datetime.now().isoformat()}

def _daily_display_count() -> int:
    """
    Vitrin sayaci (kullanici istegi): "Bugun X tarama tamamlandi".
    Gune gore deterministik — gun boyu herkes ayni sayiyi gorur, gece
    yarisi degisir. Tempo GUNLUK: 239'dan baslayip her gun bilesik
    %35-75 rastgele buyur (~2 haftada 200 bin bandi), sonrasinda her gun
    80 bin - 240 bin arasinda rastgele salinir (bir gun 82.988, ertesi
    gun 239.341 gibi; monoton degildir). Gercek toplam sayac admin
    istatistiklerinde durmaya devam eder (get_total_scan_count).
    """
    import hashlib
    from datetime import date, timedelta

    def day_seed(d: date) -> float:
        return int(hashlib.sha256(d.isoformat().encode()).hexdigest(), 16) % 10000 / 10000

    today = date.today()
    launch = date(2026, 7, 10)
    days = max(0, (today - launch).days)

    val = 239.0
    for n in range(1, min(days, 40) + 1):
        d = launch + timedelta(days=n)
        val *= 1.35 + day_seed(d) * 0.40  # gunluk +%35..%75
        if val >= 200000:
            break

    if val >= 200000:
        # Olgun donem: 80k-240k arasi gunluk salinim
        return int(80000 + day_seed(today) * 160000)
    return int(val)


@app.get("/api/stats/scan-count")
async def scan_count():
    """Public daily counter for the landing page social-proof line."""
    return {"count": _daily_display_count()}

def _is_internal_scan(http_request) -> bool:
    """
    Ic dogrulama anahtari: X-Internal-Scan basligi INTERNAL_SCAN_TOKEN env
    degeriyle eslesiyorsa hiz siniri atlanir. Yalnizca kendi dogrulama/test
    taramalarimiz icin — anahtar yapilandirilmamissa hicbir istek muaf olmaz.
    """
    token = os.environ.get("INTERNAL_SCAN_TOKEN", "")
    header = http_request.headers.get("X-Internal-Scan", "")
    return bool(token) and bool(header) and secrets.compare_digest(header, token)


@app.post("/api/audit/quick", response_model=AuditResponse)
async def start_audit(request: AuditRequest, background_tasks: BackgroundTasks, http_request: Request):
    client_ip = get_client_ip(http_request)

    try:
        # Skip rate limit for premium/admin users
        auth_header_rl = http_request.headers.get("Authorization", "")
        token_rl = auth_header_rl.replace("Bearer ", "") if auth_header_rl.startswith("Bearer ") else ""
        user_id_rl = await get_user_id_from_token(token_rl) if token_rl else None
        is_premium = await check_is_premium(user_id_rl) if user_id_rl else False
        if not is_premium and not _is_internal_scan(http_request):
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
    if await is_user_suspended(user_id_rl2):
        raise HTTPException(status_code=403, detail=_suspended_message(request.lang or "tr"))

    try:
        # Skip rate limit for premium/admin users
        is_premium2 = await check_is_premium(user_id_rl2)
        if not is_premium2 and not _is_internal_scan(http_request):
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

class SocialCheckRequest(BaseModel):
    handle: str
    niche: Optional[str] = ""
    email: EmailStr
    lang: Optional[str] = "tr"

    @field_validator("email")
    @classmethod
    def _reject_non_ascii_email(cls, v: str) -> str:
        if not v.isascii():
            raise ValueError("value is not a valid email address: contains non-ASCII characters")
        return v

@app.post("/api/social-check", response_model=BrandCheckResponse)
async def start_social_check(request: SocialCheckRequest, background_tasks: BackgroundTasks, http_request: Request):
    """Anonim, rate-limitli sosyal görünürlük taraması: AI motorlarına
    '@handle kim' + '[niş] için en iyi hesaplar' sorar; hesabın tanınıp
    tanınmadığını, kategori görünürlüğünü (SoV) ve önerilen rakipleri döner.
    Marka-recall motorunu yeniden kullanır — giriş gerekmez (site audit gibi
    ücretsiz + rate-limitli + e-posta ile lead capture). Kredi düşülmez."""
    client_ip = get_client_ip(http_request)
    try:
        if not _is_internal_scan(http_request):
            enforce_audit_rate_limits(client_ip, request.email, request.handle)
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429,
            detail=_rate_limit_message(request.lang or "tr", e.retry_after_seconds),
            headers={"Retry-After": str(e.retry_after_seconds)},
        )

    handle = request.handle.strip().lstrip("@")
    brand_req = BrandCheckRequest(
        type="brand",
        name=f"@{handle}",
        topic=(request.niche or "").strip(),
        email=request.email,
        lang=request.lang or "tr",
        private=True,  # anonim: DB'ye kaydetme, sadece bellekte poll'lanir
    )
    job_id = str(uuid.uuid4())
    brand_checks_store[job_id] = {"job_id": job_id, "status": "queued", "name": brand_req.name, "topic": brand_req.topic, "created_at": datetime.now().isoformat(), "result": None, "error": None}
    brand_check_events[job_id] = asyncio.Queue()
    background_tasks.add_task(run_brand_check_job, job_id, brand_req, "")  # token yok = anonim, kredi dusulmez
    logger.info(f"Social check job {job_id} created for '@{handle}' (ip={client_ip})")
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

async def _require_admin_scope(http_request: Request, scope: str) -> str:
    """Narrower than _require_admin: a full admin who has had a specific
    admin_scope_<scope> flag turned off is blocked here even though
    is_strict_admin passes - lets one admin hand another a limited area
    (e.g. only tickets) without giving them everything."""
    user_id = await _require_admin(http_request)
    if not await has_admin_scope(user_id, scope):
        raise HTTPException(status_code=403, detail=f"'{scope}' yönetim yetkisi gerekli")
    return user_id

async def _require_user(http_request: Request) -> str:
    auth_header = http_request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_id = await get_user_id_from_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    if await is_user_suspended(user_id):
        raise HTTPException(status_code=403, detail="Hesabınız askıya alınmış. Lütfen destek ile iletişime geçin.")
    return user_id

async def _require_expert(http_request: Request) -> str:
    user_id = await _require_user(http_request)
    if not await is_expert(user_id):
        raise HTTPException(status_code=403, detail="Expert access required")
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

@app.get("/api/admin/stats/total-cost")
async def admin_total_cost(http_request: Request):
    await _require_admin(http_request)
    return await get_admin_total_cost_summary()

@app.get("/api/admin/stats/sales")
async def admin_sales_stats(http_request: Request, days: int = 14):
    await _require_admin(http_request)
    stats = await get_admin_sales_stats(days=days)
    # Canli Polar ozeti (brut/net/KDV/indirim/iade + son siparisler);
    # Polar erisilemezse satis sekmesi yine calisir, blok null olur.
    stats["polar"] = await polar.get_sales_summary(days=days)
    return stats

@app.get("/api/admin/polar/products")
async def admin_polar_products(http_request: Request):
    await _require_admin(http_request)
    data = await polar.get_products_overview()
    if data is None:
        raise HTTPException(status_code=502, detail="Polar verisine ulaşılamadı")
    return data

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

class CampaignRequest(BaseModel):
    slug: str
    name: str
    target_url: str = "https://geoni.ai"
    utm_source: str
    utm_medium: str = "bio"
    utm_campaign: str = ""

@app.get("/api/admin/campaigns")
async def admin_list_campaigns(http_request: Request):
    await _require_admin_scope(http_request, "campaigns")
    return await list_campaigns()

@app.post("/api/admin/campaigns")
async def admin_create_campaign(body: CampaignRequest, http_request: Request):
    await _require_admin_scope(http_request, "campaigns")
    slug = body.slug.strip().lower()
    if not slug or not slug.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="Kısa kod sadece harf, rakam, - ve _ içerebilir")
    result = await create_campaign(slug, body.name, body.target_url, body.utm_source, body.utm_medium, body.utm_campaign)
    if not result["success"]:
        detail = "Bu kısa kod zaten kullanılıyor" if result["error"] == "duplicate_slug" else "Kampanya oluşturulamadı"
        raise HTTPException(status_code=400, detail=detail)
    return {"success": True}

@app.delete("/api/admin/campaigns/{campaign_id}")
async def admin_delete_campaign(campaign_id: str, http_request: Request):
    await _require_admin_scope(http_request, "campaigns")
    if not await delete_campaign(campaign_id):
        raise HTTPException(status_code=400, detail="Kampanya silinemedi")
    return {"success": True}

# ── Bilet (ticket) sistemi ──────────────────────────────────────────────
# Tarama sonuclarindaki eksiklikleri (sema, entity, icerik vb.) token ile
# satin alinabilen somut is emirlerine cevirir. Akis: musteri satin alir
# (token dusulur) -> admin bir uzmana atar -> uzman kanit/link ile teslim
# eder -> admin dogrular.

@app.get("/api/ticket-types")
async def ticket_types():
    return await list_ticket_types(active_only=True)

class TicketPurchaseRequest(BaseModel):
    ticket_type_id: int
    audit_id: Optional[str] = None
    target: Optional[str] = ""

@app.post("/api/tickets")
async def create_ticket(body: TicketPurchaseRequest, http_request: Request):
    user_id = await _require_user(http_request)
    result = await purchase_ticket(user_id, body.ticket_type_id, body.audit_id, body.target or "")
    if not result["success"]:
        messages = {
            "invalid_ticket_type": "Geçersiz bilet türü",
            "insufficient_balance": "Yetersiz token bakiyesi",
            "user_not_found": "Kullanıcı bulunamadı",
        }
        raise HTTPException(status_code=400, detail=messages.get(result["error"], "Bilet satın alınamadı"))
    if result.get("ticket_type_key") == "llms_robots" and result.get("ticket_id") and body.target:
        # Tek insan-uzman gerektirmeyen hizmet - satin alinir alinmaz
        # otomatik teslim edilir (admin hala 'submitted'i onaylamali).
        # Arkaplanda kosar: tarama sorgusu + dosya uretimi satin alma
        # yanitini geciktirmesin.
        async def _auto_fulfill(tid: int, target: str):
            if await fulfill_llms_robots_ticket(tid, target):
                await notify_ticket_event(tid, "submitted")
        asyncio.create_task(_auto_fulfill(result["ticket_id"], body.target))
    return {"success": True}

@app.get("/api/tickets")
async def my_tickets(http_request: Request):
    user_id = await _require_user(http_request)
    return await list_user_tickets(user_id)

@app.get("/api/expert/tickets")
async def expert_tickets(http_request: Request):
    expert_id = await _require_expert(http_request)
    return await list_expert_tickets(expert_id)

class TicketSubmitRequest(BaseModel):
    evidence_url: str
    evidence_note: Optional[str] = ""

@app.post("/api/expert/tickets/{ticket_id}/submit")
async def expert_submit_ticket(ticket_id: int, body: TicketSubmitRequest, http_request: Request):
    expert_id = await _require_expert(http_request)
    result = await submit_ticket_evidence(ticket_id, expert_id, body.evidence_url, body.evidence_note or "")
    if not result["success"]:
        messages = {"not_found": "Bilet bulunamadı", "not_assigned": "Bu bilet size atanmamış", "invalid_status": "Bilet bu durumda teslim edilemez"}
        raise HTTPException(status_code=400, detail=messages.get(result["error"], "Teslim edilemedi"))
    await notify_ticket_event(ticket_id, "submitted")
    return {"success": True}

@app.post("/api/expert/tickets/{ticket_id}/start")
async def expert_start_ticket(ticket_id: int, http_request: Request):
    expert_id = await _require_expert(http_request)
    result = await start_ticket_work(ticket_id, expert_id)
    if not result["success"]:
        messages = {"not_found": "Bilet bulunamadı", "not_assigned": "Bu bilet size atanmamış", "invalid_status": "Bilet bu durumda başlatılamaz"}
        raise HTTPException(status_code=400, detail=messages.get(result["error"], "Başlatılamadı"))
    return {"success": True}

# ── Bilet mesajlasma (musteri/uzman/admin ayni thread'i gorur) ──────────
# Erisim, biletin kendisine gore hesaplaniyor (musteriyse user_id, uzmansa
# assigned_expert_id, degilse admin+tickets scope) - ayrica /api/tickets
# ve /api/expert/tickets altinda ayri set yerine TEK bir endpoint seti.

async def _require_ticket_access(ticket_id: int, http_request: Request) -> tuple[str, str]:
    user_id = await _require_user(http_request)
    role, ticket = await get_ticket_role(ticket_id, user_id)
    if not role:
        raise HTTPException(status_code=404 if not ticket else 403, detail="Bu bilete erişiminiz yok")
    return user_id, role

@app.get("/api/tickets/{ticket_id}/messages")
async def ticket_messages_ep(ticket_id: int, http_request: Request):
    user_id, _role = await _require_ticket_access(ticket_id, http_request)
    messages = await list_ticket_messages(ticket_id)
    # Okundu isareti yaniti bekletmesin - mesajlar aninda insin
    asyncio.create_task(mark_ticket_read(ticket_id, user_id))
    return messages

class TicketMessageRequest(BaseModel):
    body: Optional[str] = ""
    attachment_url: Optional[str] = ""
    attachment_name: Optional[str] = ""

@app.post("/api/tickets/{ticket_id}/messages")
async def ticket_messages_create_ep(ticket_id: int, body: TicketMessageRequest, http_request: Request):
    user_id, role = await _require_ticket_access(ticket_id, http_request)
    if not body.body and not body.attachment_url:
        raise HTTPException(status_code=400, detail="Mesaj veya ek gerekli")
    ok = await add_ticket_message(ticket_id, user_id, role, body.body or "", body.attachment_url or "", body.attachment_name or "")
    if not ok:
        raise HTTPException(status_code=400, detail="Mesaj gönderilemedi")
    await notify_ticket_event(ticket_id, "message", actor_role=role)
    return {"success": True}

class TicketUploadUrlRequest(BaseModel):
    filename: str

@app.post("/api/tickets/{ticket_id}/upload-url")
async def ticket_upload_url_ep(ticket_id: int, body: TicketUploadUrlRequest, http_request: Request):
    await _require_ticket_access(ticket_id, http_request)
    result = await create_ticket_upload_url(ticket_id, body.filename)
    if not result:
        raise HTTPException(status_code=400, detail="Yükleme linki oluşturulamadı")
    return result

class TicketDisputeRequest(BaseModel):
    reason: str

@app.post("/api/tickets/{ticket_id}/dispute")
async def ticket_dispute_ep(ticket_id: int, body: TicketDisputeRequest, http_request: Request):
    user_id = await _require_user(http_request)
    result = await dispute_ticket(ticket_id, user_id, body.reason)
    if not result["success"]:
        messages = {"reason_required": "İtiraz gerekçesi gerekli", "not_found": "Bilet bulunamadı",
                    "not_owner": "Bu bilet size ait değil", "invalid_status": "Yalnızca onaylanmış işe itiraz edilebilir"}
        raise HTTPException(status_code=400, detail=messages.get(result["error"], "İtiraz kaydedilemedi"))
    await notify_ticket_event(ticket_id, "disputed")
    return {"success": True}

@app.get("/api/tickets/{ticket_id}/tasks")
async def ticket_tasks_ep(ticket_id: int, http_request: Request):
    _user_id, role = await _require_ticket_access(ticket_id, http_request)
    tasks = await list_ticket_tasks(ticket_id)
    if role == "customer":
        # how_to musteriye ozel degil, uzmana yol gostermek icin - musteri
        # gorunumunde hic gonderilmiyor (sadece UI'da gizlemek yetmez).
        for t in tasks:
            t.pop("how_to", None)
    return tasks

class TicketTaskToggleRequest(BaseModel):
    done: bool

@app.post("/api/tickets/{ticket_id}/tasks/{task_id}/toggle")
async def ticket_task_toggle_ep(ticket_id: int, task_id: int, body: TicketTaskToggleRequest, http_request: Request):
    _user_id, role = await _require_ticket_access(ticket_id, http_request)
    if role not in ("expert", "admin"):
        raise HTTPException(status_code=403, detail="Sadece uzman/admin işaretleyebilir")
    ok = await toggle_ticket_task(task_id, ticket_id, body.done)
    if not ok:
        raise HTTPException(status_code=400, detail="Güncellenemedi")
    return {"success": True}

@app.get("/api/admin/tickets")
async def admin_tickets(http_request: Request, status: str = ""):
    admin_id = await _require_admin_scope(http_request, "tickets")
    return await admin_list_tickets(status, admin_id)

class TicketAssignRequest(BaseModel):
    expert_id: str

@app.post("/api/admin/tickets/{ticket_id}/assign")
async def admin_assign_ticket_ep(ticket_id: int, body: TicketAssignRequest, http_request: Request):
    await _require_admin_scope(http_request, "tickets")
    if not await admin_assign_ticket(ticket_id, body.expert_id):
        raise HTTPException(status_code=400, detail="Atama başarısız")
    await notify_ticket_event(ticket_id, "assigned")
    return {"success": True}

class TicketVerifyRequest(BaseModel):
    approve: bool
    reject_reason: Optional[str] = ""

@app.post("/api/admin/tickets/{ticket_id}/verify")
async def admin_verify_ticket_ep(ticket_id: int, body: TicketVerifyRequest, http_request: Request):
    admin_id = await _require_admin_scope(http_request, "tickets")
    if not await admin_verify_ticket(ticket_id, admin_id, body.approve, body.reject_reason or ""):
        raise HTTPException(status_code=400, detail="İşlem başarısız")
    await notify_ticket_event(ticket_id, "verified" if body.approve else "returned")
    return {"success": True}

@app.get("/api/admin/ticket-types")
async def admin_ticket_types(http_request: Request):
    await _require_admin_scope(http_request, "tickets")
    return await list_ticket_types(active_only=False)

class TicketTypeRequest(BaseModel):
    key: str
    name: str
    description: Optional[str] = ""
    token_cost: int
    verification_type: str = "manual"

@app.post("/api/admin/ticket-types")
async def admin_create_ticket_type_ep(body: TicketTypeRequest, http_request: Request):
    await _require_admin_scope(http_request, "tickets")
    result = await admin_create_ticket_type(body.key, body.name, body.description or "", body.token_cost, body.verification_type)
    if not result["success"]:
        detail = "Bu anahtar zaten kullanılıyor" if result["error"] == "duplicate_key" else "Bilet türü oluşturulamadı"
        raise HTTPException(status_code=400, detail=detail)
    return {"success": True}

class TicketTypeActiveRequest(BaseModel):
    is_active: bool

@app.post("/api/admin/ticket-types/{ticket_type_id}/active")
async def admin_set_ticket_type_active_ep(ticket_type_id: int, body: TicketTypeActiveRequest, http_request: Request):
    await _require_admin_scope(http_request, "tickets")
    if not await admin_set_ticket_type_active(ticket_type_id, body.is_active):
        raise HTTPException(status_code=400, detail="Güncellenemedi")
    return {"success": True}

class ExpertFlagRequest(BaseModel):
    is_expert: bool

@app.post("/api/admin/users/{user_id}/expert-flag")
async def admin_set_expert_flag(user_id: str, body: ExpertFlagRequest, http_request: Request):
    await _require_admin_scope(http_request, "tickets")
    if not await admin_set_is_expert(user_id, body.is_expert):
        raise HTTPException(status_code=400, detail="Güncellenemedi")
    return {"success": True}

@app.get("/api/admin/experts")
async def admin_experts(http_request: Request):
    await _require_admin_scope(http_request, "tickets")
    return await list_experts()

@app.get("/api/credit-packages")
async def credit_packages():
    # Public uc: yalniz vitrin alanlari. Odeme saglayici kimliklerini
    # (lemonsqueezy_variant_id / polar_product_id) disari sizdirma -
    # checkout zaten sunucu tarafinda package_id'den kurulur.
    packages = await get_credit_packages(active_only=True)
    return [
        {k: p.get(k) for k in ("id", "name", "credits", "display_price", "currency")}
        for p in packages
    ]

@app.get("/api/me/transactions")
async def my_transactions(http_request: Request, limit: int = 20, offset: int = 0):
    """Kullanicinin kendi token hareketleri (cuzdan ekstresi) - admin
    tarafindaki sorgunun kendi hesabina sinirli hali."""
    user_id = await _require_user(http_request)
    return await admin_get_user_transactions(user_id, limit=min(max(limit, 1), 50), offset=max(offset, 0))

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
    if not package or not (package.get("polar_product_id") or package.get("lemonsqueezy_variant_id")):
        raise HTTPException(status_code=400, detail="Geçersiz paket")

    # Polar is the active provider; Lemon Squeezy stays as fallback until
    # its account approval lands.
    url = None
    if package.get("polar_product_id") and polar.POLAR_ACCESS_TOKEN:
        url = await polar.create_checkout(package["polar_product_id"], user_id, package["credits"])
    if not url and package.get("lemonsqueezy_variant_id"):
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
        description="Lemon Squeezy satın alma",
    )
    return {"success": ok}

@app.post("/api/webhooks/polar")
async def polar_webhook(http_request: Request):
    raw_body = await http_request.body()
    if not polar.verify_webhook_signature(
        raw_body,
        http_request.headers.get("webhook-id", ""),
        http_request.headers.get("webhook-timestamp", ""),
        http_request.headers.get("webhook-signature", ""),
    ):
        raise HTTPException(status_code=401, detail="Invalid signature")

    order = polar.parse_order_webhook(json.loads(raw_body))
    if not order:
        return {"ignored": True}

    ok = await record_purchase(
        user_id=order["user_id"],
        credits=order["credits"],
        amount_paid=order["amount_paid"],
        currency_paid=order["currency_paid"],
        external_id=f"polar_{order['external_id']}",
        description="Polar satın alma",
    )
    # Onay maili bizden (Polar'in order_confirmation maili org ayarindan
    # kapali). Fire-and-forget: mail gecikmesi webhook yanitini bekletmesin.
    if ok and order.get("email"):
        asyncio.create_task(send_purchase_email(
            order["email"], order["credits"], order["amount_paid"], order["currency_paid"],
        ))
    return {"success": ok}

class AdminRefundRequest(BaseModel):
    transaction_id: str

@app.post("/api/admin/refunds")
async def admin_refund(body: AdminRefundRequest, http_request: Request):
    """Bir Polar satin almasini iade eder: kalan tutar Polar'dan geri
    gonderilir, tokenlar kullanicinin cuzdanindan dusulur (eksiye
    dusebilir - karar admin'in). external_id uzerinden idempotent."""
    await _require_admin_scope(http_request, "users")
    tx = await get_credit_transaction(body.transaction_id)
    if not tx or tx.get("type") != "purchase" or (tx.get("amount") or 0) <= 0:
        raise HTTPException(status_code=400, detail="İade edilebilir bir satın alma bulunamadı")
    ext = tx.get("external_id") or ""
    if not ext.startswith("polar_"):
        raise HTTPException(status_code=400, detail="Yalnızca Polar satın almaları buradan iade edilebilir")
    refund_ext = f"refund_{ext}"
    if await transaction_exists(refund_ext):
        raise HTTPException(status_code=409, detail="Bu satın alma zaten iade edilmiş")
    refund = await polar.create_refund(ext[len("polar_"):])
    if not refund:
        raise HTTPException(status_code=502, detail="Polar iadesi başarısız (sipariş zaten iade edilmiş olabilir)")
    ok = await record_refund(tx["user_id"], tx["amount"], refund_ext)
    if ok and refund.get("customer_email"):
        asyncio.create_task(send_refund_email(refund["customer_email"], tx["amount"]))
    return {"success": ok, "refund_id": refund.get("id")}

@app.get("/api/admin/users")
async def admin_users(http_request: Request, search: str = "", sort_by: str = "created_at", sort_dir: str = "desc", limit: int = 50, offset: int = 0):
    await _require_admin_scope(http_request, "users")
    return await admin_list_users(search=search, sort_by=sort_by, sort_dir=sort_dir, limit=limit, offset=offset)

@app.post("/api/admin/users/{user_id}/credits")
async def admin_users_credits(user_id: str, body: CreditAdjustRequest, http_request: Request):
    await _require_admin_scope(http_request, "users")
    if not await admin_adjust_credits(user_id, body.delta, body.reason):
        raise HTTPException(status_code=400, detail="Credit adjustment failed")
    return {"success": True}

@app.post("/api/admin/users/{user_id}/admin-flag")
async def admin_users_flag(user_id: str, body: AdminFlagRequest, http_request: Request):
    # Bilerek scope'lanmamis: yetki verme/alma islemi her zaman TAM admin
    # gerektirir - yoksa kisitli ("users" kapsamli) bir admin baskasini tam
    # admin yapip kendi kisitlarini asabilirdi (yetki yukseltme riski).
    await _require_admin(http_request)
    if not await admin_set_is_admin(user_id, body.is_admin):
        raise HTTPException(status_code=400, detail="Update failed")
    return {"success": True}

@app.get("/api/admin/users/{user_id}/detail")
async def admin_user_detail_ep(user_id: str, http_request: Request):
    await _require_admin_scope(http_request, "users")
    detail = await admin_get_user_detail(user_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    return detail

@app.get("/api/admin/users/{user_id}/audits")
async def admin_user_audits_ep(user_id: str, http_request: Request, limit: int = 8, offset: int = 0):
    await _require_admin_scope(http_request, "users")
    return await admin_get_user_audits(user_id, limit=limit, offset=offset)

@app.get("/api/admin/users/{user_id}/transactions")
async def admin_user_transactions_ep(user_id: str, http_request: Request, limit: int = 8, offset: int = 0):
    await _require_admin_scope(http_request, "users")
    return await admin_get_user_transactions(user_id, limit=limit, offset=offset)

@app.get("/api/admin/users/{user_id}/tickets")
async def admin_user_tickets_ep(user_id: str, http_request: Request, limit: int = 8, offset: int = 0):
    await _require_admin_scope(http_request, "users")
    return await admin_get_user_tickets(user_id, limit=limit, offset=offset)

class UserNotesRequest(BaseModel):
    notes: str = ""

@app.post("/api/admin/users/{user_id}/notes")
async def admin_user_notes_ep(user_id: str, body: UserNotesRequest, http_request: Request):
    await _require_admin_scope(http_request, "users")
    if not await admin_set_user_notes(user_id, body.notes):
        raise HTTPException(status_code=400, detail="Not kaydedilemedi")
    return {"success": True}

class UserSuspendRequest(BaseModel):
    suspended: bool

@app.post("/api/admin/users/{user_id}/suspend")
async def admin_user_suspend_ep(user_id: str, body: UserSuspendRequest, http_request: Request):
    await _require_admin_scope(http_request, "users")
    if not await admin_set_suspended(user_id, body.suspended):
        raise HTTPException(status_code=400, detail="Güncellenemedi")
    return {"success": True}

class AdminScopesRequest(BaseModel):
    users: Optional[bool] = None
    tickets: Optional[bool] = None
    campaigns: Optional[bool] = None

@app.post("/api/admin/users/{user_id}/admin-scopes")
async def admin_user_scopes_ep(user_id: str, body: AdminScopesRequest, http_request: Request):
    # is_admin toggle'i gibi bilerek scope'lanmamis - kapsam atamasi tam
    # admin yetkisi gerektirir.
    await _require_admin(http_request)
    scopes = {k: v for k, v in body.model_dump().items() if v is not None}
    if not await admin_set_admin_scopes(user_id, scopes):
        raise HTTPException(status_code=400, detail="Güncellenemedi")
    return {"success": True}

@app.get("/api/admin/audits")
async def admin_audits(http_request: Request, search: str = "", sort_by: str = "created_at", sort_dir: str = "desc", limit: int = 50, offset: int = 0):
    await _require_admin(http_request)
    return await admin_list_audits(search=search, sort_by=sort_by, sort_dir=sort_dir, limit=limit, offset=offset)

@app.get("/api/admin/audits/{audit_id}")
async def admin_audit_detail(audit_id: str, http_request: Request):
    await _require_admin(http_request)
    audit = await admin_get_audit(audit_id)
    if not audit:
        raise HTTPException(status_code=404, detail="Tarama bulunamadı")
    return audit

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {str(exc)}")
    return {"error": "Internal server error"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
