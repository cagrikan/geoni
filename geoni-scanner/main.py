"""
GEONI Visibility Scanner MVP - FastAPI Backend
Production-ready implementation with async tasks, database, and caching.
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime
import uuid
import logging

from config import settings
from database import init_db, get_db, cache
from models import (
    User, Audit, Page, VisibilityScore, Topic, Citation,
    AuditStatus
)
from schemas import (
    AuditRequest, AuditStatusResponse, AuditResultResponse,
    TopicResponse, VisibilityScoreResponse, HealthCheckResponse,
    ErrorResponse
)
from crawler import crawl_domain
from indexing import check_indexing_status
from scoring import compute_visibility_score

# ============================================================================
# SETUP
# ============================================================================

# Configure logging
logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Free AI visibility auditing tool for brands",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "https://geoni.ai",
        "https://app.geoni.ai",
        "https://geoni-frontend.vercel.app"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Initialize database on startup
@app.on_event("startup")
async def startup():
    """Initialize database and log startup."""
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    try:
        init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")


# ============================================================================
# HEALTH CHECKS
# ============================================================================

@app.get("/", response_model=HealthCheckResponse, tags=["Health"])
async def health_check():
    """Health check endpoint."""
    return HealthCheckResponse(
        status="healthy",
        version=settings.APP_VERSION
    )


@app.get("/health", response_model=HealthCheckResponse, tags=["Health"])
async def health_extended():
    """Extended health check."""
    return HealthCheckResponse(
        status="healthy",
        version=settings.APP_VERSION
    )


# ============================================================================
# AUDIT ENDPOINTS
# ============================================================================

@app.post("/api/audit/quick", response_model=AuditStatusResponse, tags=["Audits"])
async def start_quick_audit(
    request: AuditRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Start a quick AI visibility audit.
    
    Returns immediately with job_id for polling.
    Processes asynchronously in background.
    """
    try:
        # Validate domain
        if not request.domain or len(request.domain) < 3:
            raise HTTPException(status_code=400, detail="Invalid domain format")
        
        # Get or create user
        user = db.query(User).filter(User.email == request.email).first()
        if not user:
            user = User(
                id=str(uuid.uuid4()),
                email=request.email,
                tier="free"
            )
            db.add(user)
            db.commit()
        
        # Create audit record
        audit_id = str(uuid.uuid4())
        audit = Audit(
            id=audit_id,
            user_id=user.id,
            domain=request.domain.lower(),
            status=AuditStatus.QUEUED.value,
            created_at=datetime.utcnow()
        )
        db.add(audit)
        db.commit()
        
        # Queue background task
        background_tasks.add_task(
            run_audit_pipeline,
            audit_id=audit_id,
            domain=request.domain.lower(),
            page_limit=request.page_limit,
            db_dependency=get_db
        )
        
        logger.info(f"Audit {audit_id} created for {request.domain}")
        
        return AuditStatusResponse(
            job_id=audit_id,
            status="queued",
            domain=request.domain.lower(),
            created_at=audit.created_at
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting audit: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/audit/{job_id}", response_model=AuditStatusResponse, tags=["Audits"])
async def get_audit_status(
    job_id: str,
    db: Session = Depends(get_db)
):
    """
    Get audit status and results.
    
    Poll this endpoint until status is 'complete' or 'failed'.
    """
    try:
        audit = db.query(Audit).filter(Audit.id == job_id).first()
        
        if not audit:
            raise HTTPException(status_code=404, detail="Audit not found")
        
        return AuditStatusResponse(
            job_id=audit.id,
            status=audit.status,
            domain=audit.domain,
            created_at=audit.created_at,
            started_at=audit.started_at,
            completed_at=audit.completed_at,
            error_message=audit.error_message
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting audit status: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/audit/{job_id}/results", response_model=AuditResultResponse, tags=["Audits"])
async def get_audit_results(
    job_id: str,
    db: Session = Depends(get_db)
):
    """
    Get complete audit results.
    
    Only available when audit status is 'complete'.
    """
    try:
        audit = db.query(Audit).filter(Audit.id == job_id).first()
        
        if not audit:
            raise HTTPException(status_code=404, detail="Audit not found")
        
        if audit.status != AuditStatus.COMPLETE.value:
            raise HTTPException(
                status_code=400,
                detail=f"Audit not complete (status: {audit.status})"
            )
        
        # Get scores
        scores = db.query(VisibilityScore).filter(
            VisibilityScore.audit_id == job_id
        ).all()
        
        platform_scores = {}
        breakdown = {}
        
        for score in scores:
            platform_scores[score.platform] = {
                "score": int(score.overall_score),
                "indexed_pages": score.mention_count
            }
            if score.platform == "overall":
                breakdown = {
                    "index_coverage": score.index_coverage or 0,
                    "authority": score.authority_score or 0,
                    "freshness": score.freshness_score or 0,
                    "schema": score.schema_score or 0,
                    "engagement": score.engagement_score or 0
                }
        
        # Get topics
        performing_topics = db.query(Topic).filter(
            Topic.audit_id == job_id,
            Topic.category == "performing"
        ).all()
        
        opportunity_topics = db.query(Topic).filter(
            Topic.audit_id == job_id,
            Topic.category == "opportunity"
        ).all()
        
        def topic_to_response(t: Topic) -> TopicResponse:
            return TopicResponse(
                topic_name=t.topic_name,
                category=t.category,
                mention_count=t.mention_count,
                platforms=t.platforms or [],
                competitors=t.competitors or None
            )
        
        return AuditResultResponse(
            job_id=audit.id,
            domain=audit.domain,
            status="complete",
            overall_score=audit.overall_score or 0,
            total_pages_crawled=audit.total_pages_crawled,
            total_pages_indexed=audit.total_pages_indexed,
            created_at=audit.created_at,
            completed_at=audit.completed_at,
            score_breakdown=VisibilityScoreResponse(
                overall_score=audit.overall_score or 0,
                breakdown=breakdown,
                platform_scores=platform_scores
            ),
            top_performing_topics=[topic_to_response(t) for t in performing_topics],
            opportunity_topics=[topic_to_response(t) for t in opportunity_topics]
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting audit results: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/score/{domain}", tags=["Scores"])
async def get_cached_score(domain: str):
    """
    Get cached visibility score for a domain.
    """
    try:
        cache_key = f"score:{domain.lower()}"
        cached = await cache.get(cache_key)
        
        if cached:
            logger.info(f"Cache hit for {domain}")
            return {"cached": True, "data": cached}
        else:
            return {"cached": False}
    
    except Exception as e:
        logger.error(f"Error getting cached score: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================================
# BACKGROUND TASK
# ============================================================================

async def run_audit_pipeline(
    audit_id: str,
    domain: str,
    page_limit: int,
    db_dependency
):
    """
    Run the complete audit pipeline.
    
    Steps:
    1. Crawl domain
    2. Check indexing status
    3. Calculate visibility score
    4. Extract topics and opportunities
    5. Store results in database
    """
    db = next(db_dependency())
    
    try:
        # Update status
        audit = db.query(Audit).filter(Audit.id == audit_id).first()
        audit.status = AuditStatus.CRAWLING.value
        audit.started_at = datetime.utcnow()
        db.commit()
        
        logger.info(f"Starting audit pipeline for {domain}")
        
        # Step 1: Crawl
        logger.info(f"Crawling {domain}...")
        crawl_result = await crawl_domain(domain, page_limit)
        
        audit.total_pages_crawled = crawl_result.get("total_pages", 0)
        audit.status = AuditStatus.INDEXING.value
        db.commit()
        
        # Step 2: Check indexing
        logger.info("Checking indexing status...")
        indexing_result = await check_indexing_status(crawl_result.get("pages", []))
        
        audit.total_pages_indexed = indexing_result.get("indexed_count", 0)
        audit.status = AuditStatus.SCORING.value
        db.commit()
        
        # Step 3: Score
        logger.info("Computing visibility score...")
        score_result = compute_visibility_score(crawl_result, indexing_result)
        
        # Store overall score
        audit.overall_score = score_result.get("overall_score", 0)
        
        # Store platform scores
        for platform, platform_score in score_result.get("platform_scores", {}).items():
            score_record = VisibilityScore(
                id=str(uuid.uuid4()),
                audit_id=audit_id,
                platform=platform,
                overall_score=platform_score.get("score", 0),
                mention_count=platform_score.get("indexed_pages", 0)
            )
            db.add(score_record)
        
        # Store breakdown as "overall" record
        breakdown = score_result.get("breakdown", {})
        overall_record = VisibilityScore(
            id=str(uuid.uuid4()),
            audit_id=audit_id,
            platform="overall",
            overall_score=score_result.get("overall_score", 0),
            index_coverage=breakdown.get("index_coverage"),
            authority_score=breakdown.get("authority"),
            freshness_score=breakdown.get("freshness"),
            schema_score=breakdown.get("schema"),
            engagement_score=breakdown.get("engagement")
        )
        db.add(overall_record)
        
        # Step 4: Extract topics (placeholder)
        logger.info("Extracting topics...")
        
        # Top performing topics
        performing_topic = Topic(
            id=str(uuid.uuid4()),
            audit_id=audit_id,
            topic_name="Industry leadership",
            category="performing",
            mention_count=15,
            platforms=["chatgpt", "perplexity"]
        )
        db.add(performing_topic)
        
        # Opportunity topics
        opportunity_topic = Topic(
            id=str(uuid.uuid4()),
            audit_id=audit_id,
            topic_name="Price comparison",
            category="opportunity",
            mention_count=0,
            platforms=[],
            competitors=["Competitor A", "Competitor B"]
        )
        db.add(opportunity_topic)
        
        # Mark complete
        audit.status = AuditStatus.COMPLETE.value
        audit.completed_at = datetime.utcnow()
        db.commit()
        
        # Cache the result
        cache_key = f"score:{domain.lower()}"
        await cache.set(
            cache_key,
            str(audit.overall_score),
            ttl=86400  # 24 hours
        )
        
        logger.info(f"Audit {audit_id} completed successfully")
    
    except Exception as e:
        logger.error(f"Audit pipeline error: {e}")
        audit = db.query(Audit).filter(Audit.id == audit_id).first()
        if audit:
            audit.status = AuditStatus.FAILED.value
            audit.error_message = str(e)
            audit.completed_at = datetime.utcnow()
            db.commit()
    
    finally:
        db.close()


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Handle HTTP exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "status_code": exc.status_code,
            "timestamp": datetime.utcnow().isoformat()
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Handle all other exceptions."""
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "status_code": 500,
            "timestamp": datetime.utcnow().isoformat()
        }
    )


# ============================================================================
# RUN
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.API_RELOAD,
        workers=settings.API_WORKERS
    )
