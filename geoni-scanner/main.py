"""
GEONI Visibility Scanner MVP - FastAPI Backend
Now using real Playwright crawler, indexing checks, and scoring engine.
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
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

class AuditRequest(BaseModel):
    domain: str
    email: EmailStr
    competitors: Optional[List[str]] = None
    page_limit: int = 500

class AuditResponse(BaseModel):
    job_id: str
    status: str
    estimated_time: int

app = FastAPI(title="GEONI Visibility Scanner MVP", version="0.2.0", description="AI visibility auditing tool with real crawling, indexing, and scoring")

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


async def run_audit_job(job_id: str, request: AuditRequest):
    try:
        jobs_store[job_id]["status"] = "crawling"
        crawl_result = await crawl_domain(request.domain, request.page_limit)

        jobs_store[job_id]["status"] = "indexing"
        indexing_status = await check_indexing_status(crawl_result["pages"])

        jobs_store[job_id]["status"] = "scoring"
        score_result = await compute_ai_visibility_score(crawl_result, indexing_status)

        topics = await generate_topics_and_opportunities(request.domain, crawl_result["pages"])

        jobs_store[job_id].update({
            "status": "complete",
            "result": {
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
                "created_at": datetime.now().isoformat()
            },
            "completed_at": datetime.now().isoformat()
        })

        logger.info(f"Audit job {job_id} completed successfully")

    except Exception as e:
        logger.error(f"Audit job {job_id} failed: {str(e)}")
        jobs_store[job_id]["status"] = "failed"
        jobs_store[job_id]["error"] = str(e)

@app.get("/")
@app.get("/health")
async def health():
    return {"status": "healthy", "version": "0.2.0", "timestamp": datetime.now().isoformat()}

@app.post("/api/audit/quick", response_model=AuditResponse)
async def start_audit(request: AuditRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    jobs_store[job_id] = {"job_id": job_id, "status": "queued", "domain": request.domain, "email": request.email, "created_at": datetime.now().isoformat(), "result": None, "error": None}
    background_tasks.add_task(run_audit_job, job_id, request)
    logger.info(f"Audit job {job_id} created for {request.domain}")
    return AuditResponse(job_id=job_id, status="queued", estimated_time=300)

@app.get("/api/audit/{job_id}")
async def get_audit_status(job_id: str):
    if job_id not in jobs_store:
        raise HTTPException(status_code=404, detail="Audit job not found")
    job = jobs_store[job_id]
    if job["status"] == "complete":
        return {"job_id": job_id, "status": "complete", "result": job["result"]}
    elif job["status"] == "failed":
        raise HTTPException(status_code=500, detail=f"Audit failed: {job['error']}")
    else:
        return {"job_id": job_id, "status": job["status"], "created_at": job["created_at"]}

@app.get("/api/audit/{job_id}/results")
async def get_audit_results(job_id: str):
    return await get_audit_status(job_id)

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
