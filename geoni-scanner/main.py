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
from db import save_audit, save_brand_check, get_user_id_from_token, check_is_premium, get_total_scan_count

class AuditRequest(BaseModel):
    domain: str
    email: EmailStr
    competitors: Optional[List[str]] = None
    page_limit: int = 500

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

    def emit(message: str):
        if queue is not None:
            queue.put_nowait(message)

    try:
        jobs_store[job_id]["status"] = "crawling"
        emit(f"{request.domain} taranıyor…")
        crawl_result = await crawl_domain(request.domain, request.page_limit)
        emit(f"{crawl_result['total_pages']} sayfa tarandı ✓")

        jobs_store[job_id]["status"] = "indexing"
        emit("AI botlarının erişimi kontrol ediliyor…")
        indexing_status = await check_indexing_status(crawl_result["pages"])
        emit("Dizin durumu kontrol edildi ✓")

        jobs_store[job_id]["status"] = "scoring"

        # Infer brand name + topic from crawled page titles, then check
        # whether the LLM's trained knowledge already recognizes this brand
        # within that topic. This becomes a 6th scoring dimension.
        page_titles = [p.get("title", "") for p in crawl_result.get("pages", []) if p.get("title")]
        identity = await infer_brand_identity(request.domain, page_titles)
        brand_recall_result = await check_brand_recall(identity["name"], identity["topic"], on_progress=emit)
        emit("Skor hesaplanıyor…")

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

        # Save to Supabase
        user_id = await get_user_id_from_token(token) if token else None
        await save_audit(job_id, {"domain": request.domain, "email": request.email}, jobs_store[job_id]["result"], user_id)
        logger.info(f"Audit job {job_id} completed successfully")

        # Fire-and-forget email delivery. send_audit_report_email never raises,
        # so a failed/unconfigured email send cannot affect the audit's success.
        email_sent = await send_audit_report_email(request.email, request.domain, result_payload)
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
        # Save to Supabase
        user_id = await get_user_id_from_token(token) if token else None
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
            detail=f"Çok fazla istek gönderdiniz. Lütfen {e.retry_after_seconds} saniye sonra tekrar deneyin.",
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

    try:
        # Skip rate limit for premium/admin users
        auth_header_rl2 = http_request.headers.get("Authorization", "")
        token_rl2 = auth_header_rl2.replace("Bearer ", "") if auth_header_rl2.startswith("Bearer ") else ""
        user_id_rl2 = await get_user_id_from_token(token_rl2) if token_rl2 else None
        is_premium2 = await check_is_premium(user_id_rl2) if user_id_rl2 else False
        if not is_premium2:
            enforce_audit_rate_limits(client_ip, request.email, request.name)
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429,
            detail=f"Çok fazla istek gönderdiniz. Lütfen {e.retry_after_seconds} saniye sonra tekrar deneyin.",
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

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {str(exc)}")
    return {"error": "Internal server error"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
