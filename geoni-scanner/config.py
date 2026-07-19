"""
Configuration module for GEONI Visibility Scanner.
Loads from .env and environment variables.
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings."""
    
    # Core
    APP_NAME: str = "GEONI Visibility Scanner MVP"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    
    # API
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_RELOAD: bool = False
    API_WORKERS: int = 4
    
    # Database
    DATABASE_URL: str = "postgresql://user:password@localhost:5432/geoni_scanner"
    DATABASE_ECHO: bool = False
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_TTL: int = 3600  # 1 hour default TTL
    
    # Crawler settings
    CRAWLER_MAX_PAGES: int = 500
    CRAWLER_TIMEOUT_PER_PAGE: int = 10  # seconds
    CRAWLER_TOTAL_TIMEOUT: int = 300  # 5 minutes
    CRAWLER_DELAY_MS: int = 500  # Respectful crawling delay
    CRAWLER_USER_AGENT: str = "GEONI-Scanner/1.0 (+https://geoni.ai/bot)"
    
    # Job queue (Celery)
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND_URL: str = "redis://localhost:6379/2"
    CELERY_TASK_TIME_LIMIT: int = 600  # 10 minutes
    
    # External APIs
    GOOGLE_API_KEY: Optional[str] = None
    GOOGLE_SEARCH_ENGINE_ID: Optional[str] = None
    BING_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    
    # CORS
    CORS_ORIGINS: list = ["http://localhost:3000", "https://geoni.ai"]

    # Auth: token dogrulama Supabase GoTrue'ya delege edilir (db.get_user_id_from_token
    # -> /auth/v1/user). Lokal JWT imza/dogrulama YOK; eski JWT_SECRET_KEY/HS256 config
    # olu ve yaniltici oldugu icin kaldirildi (QA 2026-07-19 guvenlik denetimi).

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
