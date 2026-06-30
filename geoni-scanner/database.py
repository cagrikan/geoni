"""
Database connection and session management for GEONI.
Handles PostgreSQL connections and Redis caching.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool
import redis
import logging
from config import settings
from models import Base

logger = logging.getLogger(__name__)

# PostgreSQL Engine
engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DATABASE_ECHO,
    poolclass=NullPool,  # Disable connection pooling for Celery workers
    connect_args={"connect_timeout": 10}
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Redis connection
try:
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    redis_client.ping()
    logger.info("Redis connected successfully")
except Exception as e:
    logger.error(f"Failed to connect to Redis: {e}")
    redis_client = None


def get_db() -> Session:
    """Dependency for FastAPI to get DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables in the database."""
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialized")


def get_redis():
    """Get Redis client for caching."""
    if not redis_client:
        raise RuntimeError("Redis connection failed")
    return redis_client


class Cache:
    """Cache utility wrapper for Redis."""
    
    def __init__(self, client=None):
        self.client = client or redis_client
    
    async def get(self, key: str):
        """Get value from cache."""
        if not self.client:
            return None
        try:
            return self.client.get(key)
        except Exception as e:
            logger.error(f"Cache GET error for {key}: {e}")
            return None
    
    async def set(self, key: str, value: str, ttl: int = None):
        """Set value in cache with optional TTL."""
        if not self.client:
            return False
        try:
            ttl = ttl or settings.REDIS_TTL
            self.client.setex(key, ttl, value)
            return True
        except Exception as e:
            logger.error(f"Cache SET error for {key}: {e}")
            return False
    
    async def delete(self, key: str):
        """Delete key from cache."""
        if not self.client:
            return False
        try:
            self.client.delete(key)
            return True
        except Exception as e:
            logger.error(f"Cache DELETE error for {key}: {e}")
            return False
    
    async def flush_pattern(self, pattern: str):
        """Delete all keys matching pattern."""
        if not self.client:
            return False
        try:
            keys = self.client.keys(pattern)
            if keys:
                self.client.delete(*keys)
            return True
        except Exception as e:
            logger.error(f"Cache FLUSH error for pattern {pattern}: {e}")
            return False


# Initialize cache instance
cache = Cache()
