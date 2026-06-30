"""
Pydantic request and response schemas for API validation.
"""

from pydantic import BaseModel, EmailStr, HttpUrl, Field
from typing import Optional, List, Dict
from datetime import datetime


# ============================================================================
# REQUEST SCHEMAS
# ============================================================================

class AuditRequest(BaseModel):
    """Request schema for starting an audit."""
    domain: str = Field(..., description="Domain to audit (e.g., example.com)")
    email: EmailStr = Field(..., description="Email for results delivery")
    competitors: Optional[List[str]] = Field(
        default=None,
        description="List of competitor domains to compare against"
    )
    page_limit: int = Field(
        default=500,
        ge=10,
        le=1000,
        description="Maximum pages to crawl (10-1000)"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "domain": "example.com",
                "email": "user@company.com",
                "competitors": ["competitor1.com", "competitor2.com"],
                "page_limit": 500
            }
        }


# ============================================================================
# RESPONSE SCHEMAS
# ============================================================================

class AuditStatusResponse(BaseModel):
    """Response schema for audit status."""
    job_id: str
    status: str  # queued, crawling, indexing, scoring, complete, failed
    domain: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "queued",
                "domain": "example.com",
                "created_at": "2026-06-30T10:00:00Z",
                "started_at": None,
                "completed_at": None,
                "error_message": None
            }
        }


class TopicResponse(BaseModel):
    """Response schema for a topic."""
    topic_name: str
    category: str  # performing, opportunity
    mention_count: int
    platforms: List[str] = []
    competitors: Optional[List[str]] = None


class ScorePlatformBreakdown(BaseModel):
    """Score breakdown for a single platform."""
    score: int
    indexed_pages: int


class VisibilityScoreResponse(BaseModel):
    """Response schema for visibility score."""
    overall_score: int
    breakdown: Dict[str, float]
    platform_scores: Dict[str, ScorePlatformBreakdown]
    
    class Config:
        json_schema_extra = {
            "example": {
                "overall_score": 72,
                "breakdown": {
                    "index_coverage": 85.0,
                    "authority": 68.0,
                    "freshness": 75.0,
                    "schema": 60.0,
                    "engagement": 70.0
                },
                "platform_scores": {
                    "chatgpt": {"score": 78, "indexed_pages": 42},
                    "perplexity": {"score": 72, "indexed_pages": 38},
                    "google_ai": {"score": 68, "indexed_pages": 35}
                }
            }
        }


class AuditResultResponse(BaseModel):
    """Complete audit result response."""
    job_id: str
    domain: str
    status: str = "complete"
    overall_score: int
    total_pages_crawled: int
    total_pages_indexed: int
    created_at: datetime
    completed_at: datetime
    
    # Detailed results
    score_breakdown: VisibilityScoreResponse
    top_performing_topics: List[TopicResponse]
    opportunity_topics: List[TopicResponse]
    competitor_analysis: Optional[Dict] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "domain": "example.com",
                "status": "complete",
                "overall_score": 72,
                "total_pages_crawled": 150,
                "total_pages_indexed": 135,
                "created_at": "2026-06-30T10:00:00Z",
                "completed_at": "2026-06-30T10:05:30Z",
                "score_breakdown": {
                    "overall_score": 72,
                    "breakdown": {},
                    "platform_scores": {}
                },
                "top_performing_topics": [],
                "opportunity_topics": []
            }
        }


class ErrorResponse(BaseModel):
    """Error response schema."""
    detail: str
    status_code: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_schema_extra = {
            "example": {
                "detail": "Invalid domain format",
                "status_code": 400,
                "timestamp": "2026-06-30T10:00:00Z"
            }
        }


class HealthCheckResponse(BaseModel):
    """Health check response."""
    status: str = "healthy"
    version: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "healthy",
                "version": "0.1.0",
                "timestamp": "2026-06-30T10:00:00Z"
            }
        }
