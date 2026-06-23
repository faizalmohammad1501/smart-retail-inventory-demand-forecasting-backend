from pydantic_settings import BaseSettings
from typing import List, Optional


class Settings(BaseSettings):
    # ── Application ──────────────────────────────────────────────────────────
    APP_NAME: str = "Smart Retail Analytics API"
    APP_VERSION: str = "2.0.0"
    APP_ENV: str = "development"          # development | staging | production
    LOG_LEVEL: str = "INFO"

    # ── JWT Authentication ────────────────────────────────────────────────────
    JWT_SECRET_KEY: str = "smart-retail-super-secret-jwt-key-change-in-production-32chars"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite:///./supply_chain.db"

    # ── CORS ─────────────────────────────────────────────────────────────────
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # ── Rate limiting (requests per minute per IP) ────────────────────────────
    RATE_LIMIT_PER_MINUTE: int = 120
    LOGIN_RATE_LIMIT_PER_MINUTE: int = 5      # tighter limit for auth endpoints
    REGISTER_RATE_LIMIT_PER_MINUTE: int = 10

    # ── Request validation ────────────────────────────────────────────────────
    MAX_REQUEST_SIZE_BYTES: int = 1_048_576   # 1 MB

    # ── Caching ───────────────────────────────────────────────────────────────
    CACHE_DEFAULT_TTL_SECONDS: int = 300      # 5 min
    CACHE_SWEEP_INTERVAL_SECONDS: int = 60

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
