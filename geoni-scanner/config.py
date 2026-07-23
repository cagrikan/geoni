"""
Configuration module for GEONI Visibility Scanner.
Loads from .env and environment variables.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
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

    # NOT: Celery/DB-pool eski plandi; gercek mimari SQS (scanqueue.py/worker.py).
    # REDIS_URL rezerve tutuluyor (olceklenince Upstash-free ile paylasimli
    # rate-limit icin; su an in-memory + App Runner MaxSize=1).

    # External APIs
    GOOGLE_API_KEY: Optional[str] = None
    GOOGLE_SEARCH_ENGINE_ID: Optional[str] = None
    BING_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    # Grok (xAI) — SHADOW recall motoru (brand_recall WEIGHTS['grok']=0, manseti
    # degistirmez). XAI_MODEL anahtar eklenirken dogru model id ile teyit edilir.
    XAI_API_KEY: Optional[str] = None
    XAI_MODEL: Optional[str] = None
    # grok-web SOV motoru (Agent Tools API, web+x search). SHADOW + env-kapili
    # (GROK_WEB_SHADOW=1 ile acilir; varsayilan KAPALI — pahali/yavas). TUM arama
    # tiplerinde SOV'da golge kosar. GROK_WEB_DAILY_CAP: gunluk grok_web cagri tavani
    # (para kacmasin; ~$0.10/cagri, cap=5 ≈ ~$0.50/gun). Kod default 5.
    XAI_WEB_MODEL: Optional[str] = None
    GROK_WEB_SHADOW: Optional[str] = None
    GROK_WEB_DAILY_CAP: Optional[str] = None

    # CORS
    CORS_ORIGINS: list = ["http://localhost:3000", "https://geoni.ai"]

    # Auth: token dogrulama Supabase GoTrue'ya delege edilir (db.get_user_id_from_token
    # -> /auth/v1/user). Lokal JWT imza/dogrulama YOK; eski JWT_SECRET_KEY/HS256 config
    # olu ve yaniltici oldugu icin kaldirildi (QA 2026-07-19 guvenlik denetimi).

    # pydantic-settings v2: eski nested `class Config` yerine (2.0'dan beri deprecated).
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


settings = Settings()
