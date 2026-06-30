"""
Database models for GEONI Visibility Scanner.
Uses SQLAlchemy ORM with PostgreSQL.
"""

from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, DateTime, 
    ForeignKey, Text, JSON, Boolean, Index, Enum
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import uuid
import enum

Base = declarative_base()


class AuditStatus(str, enum.Enum):
    """Audit job status enum."""
    QUEUED = "queued"
    CRAWLING = "crawling"
    INDEXING = "indexing"
    SCORING = "scoring"
    COMPLETE = "complete"
    FAILED = "failed"


class User(Base):
    """User account model."""
    __tablename__ = "users"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, index=True, nullable=False)
    company_name = Column(String(255), nullable=True)
    tier = Column(String(50), default="free")  # free, pro, enterprise
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    audits = relationship("Audit", back_populates="user", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("idx_users_email", "email"),
        Index("idx_users_tier", "tier"),
    )


class Audit(Base):
    """Audit job model."""
    __tablename__ = "audits"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    domain = Column(String(255), nullable=False, index=True)
    status = Column(String(50), default=AuditStatus.QUEUED.value, index=True)
    
    # Results
    overall_score = Column(Integer, nullable=True)
    total_pages_crawled = Column(Integer, default=0)
    total_pages_indexed = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="audits")
    pages = relationship("Page", back_populates="audit", cascade="all, delete-orphan")
    scores = relationship("VisibilityScore", back_populates="audit", cascade="all, delete-orphan")
    topics = relationship("Topic", back_populates="audit", cascade="all, delete-orphan")
    citations = relationship("Citation", back_populates="audit", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("idx_audits_user_domain", "user_id", "domain"),
        Index("idx_audits_status_created", "status", "created_at"),
    )


class Page(Base):
    """Crawled page model."""
    __tablename__ = "pages"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id = Column(String(36), ForeignKey("audits.id"), nullable=False)
    url = Column(String(2048), nullable=False)
    title = Column(String(500), nullable=True)
    meta_description = Column(String(500), nullable=True)
    h1_text = Column(String(500), nullable=True)
    canonical_url = Column(String(2048), nullable=True)
    
    # Indexing status
    indexed_google = Column(Boolean, default=False)
    indexed_bing = Column(Boolean, default=False)
    indexed_openai = Column(Boolean, default=False)
    indexed_anthropic = Column(Boolean, default=False)
    
    # Content signals
    last_modified = Column(DateTime, nullable=True)
    word_count = Column(Integer, nullable=True)
    has_schema_markup = Column(Boolean, default=False)
    
    # Timestamps
    crawled_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    audit = relationship("Audit", back_populates="pages")
    
    __table_args__ = (
        Index("idx_pages_audit_url", "audit_id", "url"),
    )


class VisibilityScore(Base):
    """AI Visibility Score breakdown model."""
    __tablename__ = "visibility_scores"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id = Column(String(36), ForeignKey("audits.id"), nullable=False)
    platform = Column(String(50), nullable=False)  # chatgpt, perplexity, google_ai, etc.
    
    # Score components
    overall_score = Column(Float, nullable=False)
    index_coverage = Column(Float, nullable=True)
    authority_score = Column(Float, nullable=True)
    freshness_score = Column(Float, nullable=True)
    schema_score = Column(Float, nullable=True)
    engagement_score = Column(Float, nullable=True)
    
    # Mentions
    mention_count = Column(Integer, default=0)
    
    # Timestamps
    calculated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    audit = relationship("Audit", back_populates="scores")
    
    __table_args__ = (
        Index("idx_scores_audit_platform", "audit_id", "platform"),
    )


class Topic(Base):
    """Topic model (performing and opportunity)."""
    __tablename__ = "topics"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id = Column(String(36), ForeignKey("audits.id"), nullable=False)
    topic_name = Column(String(500), nullable=False)
    category = Column(String(50), nullable=False)  # performing, opportunity
    mention_count = Column(Integer, default=0)
    platforms = Column(JSON, default=[])  # ["chatgpt", "perplexity", ...]
    competitors = Column(JSON, default=[])  # Competing brands visible for this topic
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    audit = relationship("Audit", back_populates="topics")
    
    __table_args__ = (
        Index("idx_topics_audit_category", "audit_id", "category"),
    )


class Citation(Base):
    """Citation tracking model."""
    __tablename__ = "citations"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id = Column(String(36), ForeignKey("audits.id"), nullable=False)
    source_domain = Column(String(255), nullable=False)  # Domain citing your domain
    cited_page_url = Column(String(2048), nullable=False)  # Page in source_domain
    frequency = Column(Integer, default=1)  # How many times cited
    context = Column(Text, nullable=True)  # Snippet of citation context
    
    # Timestamps
    discovered_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    audit = relationship("Audit", back_populates="citations")
    
    __table_args__ = (
        Index("idx_citations_audit_source", "audit_id", "source_domain"),
    )
