"""
GEONI Visibility Scanner MVP - FastAPI Backend
Real Playwright crawler, indexing checks, scoring engine (now with brand
recall as a 6th dimension), multi-dimensional rate limiting, automatic email
report delivery, and a standalone brand-recall-only check for people/brands
without a website (e.g. political candidates, executives).
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import Optional, List
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
    name: str
    topic: Optional[str] = ""
    email: Optional[str] = "anonymous@geoni.ai"

class BrandCheckResponse(BaseModel):
    job_id: str
    status: str

app = FastAPI(title="GEONI Visibility Scanner MVP", version="0.6.0", description="AI visibility auditing with real crawling, indexing, 6-dimension scoring (incl. brand recall), rate limiting, and email delivery")

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


async def run_audit_job(job_id: str, request: AuditRequest):
    try:
        jobs_store[job_id]["status"] = "crawling"
        crawl_result = await crawl_domain(request.domain, request.page_limit)

        jobs_store[job_id]["status"] = "indexing"
        indexing_status = await check_indexing_status(crawl_result["pages"])

        jobs_store[job_id]["status"] = "scoring"

        # Infer brand name + topic from crawled page titles, then check
        # whether the LLM's trained knowledge already recognizes this brand
        # within that topic. This becomes a 6th scoring dimension.
        page_titles = [p.get("title", "") for p in crawl_result.get("pages", []) if p.get("title")]
        identity = await infer_brand_identity(request.domain, page_titles)
        brand_recall_result = await check_brand_recall(identity["name"], identity["topic"])

        score_result = await compute_ai_visibility_score(crawl_result, indexing_status, brand_recall_result)

        topics = await generate_topics_and_opportunities(request.domain, crawl_result["pages"])

        result_payload = {
            "domain": request.domain,
            "score": score_result["overall_score"],
            "score_breakdown": score_result["breakdown"],
            "total_pages": crawl_result["total_pages"],
            "indexed_pages": indexing_status["indexed_count"],
            "platforms": {
                "chatgpt": indexing_status.get("openai", False),
                "anthropic": indexing_status.get("anthropic", False),
                "google": indexing_status.get("google", 0),
                "bing": indexing_status.get("bing", 0),
            },
            "top_topics": topics["performing_topics"],
            "opportunities": topics["opportunity_topics"],
            "brand_recall": {
                "checked": brand_recall_result.get("checked", False),
                "recognized": brand_recall_result.get("recognized", False),
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

        logger.info(f"Audit job {job_id} completed successfully")

        # Fire-and-forget email delivery. send_audit_report_email never raises,
        # so a failed/unconfigured email send cannot affect the audit's success.
        email_sent = await send_audit_report_email(request.email, request.domain, result_payload)
        jobs_store[job_id]["email_sent"] = email_sent

    except Exception as e:
        logger.error(f"Audit job {job_id} failed: {str(e)}")
        jobs_store[job_id]["status"] = "failed"
        jobs_store[job_id]["error"] = str(e)


async def run_brand_check_job(job_id: str, request: BrandCheckRequest):
    """
    Standalone brand-recall-only check for people/brands without a website
    (e.g. political candidates, executives, personal brands). No crawling —
    just the same knowledge-recall query used by the geoni.ai widget, kept
    consistent so results match whether run there or here.
    """
    try:
        result = await check_brand_recall(request.name, request.topic, request.email or "")
        brand_checks_store[job_id].update({
            "status": "complete",
            "result": {
                "name": request.name,
                "topic": request.topic,
                "recognized": result.get("recognized", False),
                "recognition_count": result.get("recognition_count", 0),
                "model_results": result.get("model_results", {}),
                "checked": result.get("checked", False),
                "raw_list": result.get("raw_list"),
                "created_at": datetime.now().isoformat(),
            },
            "completed_at": datetime.now().isoformat(),
        })
        logger.info(f"Brand check job {job_id} completed for '{request.name}'")
    except Exception as e:
        logger.error(f"Brand check job {job_id} failed: {str(e)}")
        brand_checks_store[job_id]["status"] = "failed"
        brand_checks_store[job_id]["error"] = str(e)


@app.get("/")
@app.get("/health")
async def health():
    return {"status": "healthy", "version": "0.6.0", "timestamp": datetime.now().isoformat()}

@app.post("/api/audit/quick", response_model=AuditResponse)
async def start_audit(request: AuditRequest, background_tasks: BackgroundTasks, http_request: Request):
    client_ip = get_client_ip(http_request)

    try:
        enforce_audit_rate_limits(client_ip, request.email, request.domain)
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429,
            detail=f"Çok fazla istek gönderdiniz. Lütfen {e.retry_after_seconds} saniye sonra tekrar deneyin.",
            headers={"Retry-After": str(e.retry_after_seconds)},
        )

    job_id = str(uuid.uuid4())
    jobs_store[job_id] = {"job_id": job_id, "status": "queued", "domain": request.domain, "email": request.email, "created_at": datetime.now().isoformat(), "result": None, "error": None}
    background_tasks.add_task(run_audit_job, job_id, request)
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
        enforce_audit_rate_limits(client_ip, request.email, request.name)
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429,
            detail=f"Çok fazla istek gönderdiniz. Lütfen {e.retry_after_seconds} saniye sonra tekrar deneyin.",
            headers={"Retry-After": str(e.retry_after_seconds)},
        )

    job_id = str(uuid.uuid4())
    brand_checks_store[job_id] = {"job_id": job_id, "status": "queued", "name": request.name, "topic": request.topic, "created_at": datetime.now().isoformat(), "result": None, "error": None}
    background_tasks.add_task(run_brand_check_job, job_id, request)
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
