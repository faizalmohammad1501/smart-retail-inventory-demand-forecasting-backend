from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # JWT Settings
    JWT_SECRET_KEY: str = "smart-retail-super-secret-jwt-key-change-in-production-32chars"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Database
    DATABASE_URL: str = "sqlite:///./supply_chain.db"

    # App
    APP_NAME: str = "Supply Chain Analytics API"
    APP_VERSION: str = "2.0.0"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
