"""
Omnipath Configuration Management
Centralized configuration with environment variable support and validation.
"""

from pydantic_settings import BaseSettings
from pydantic import Field, validator
import secrets


class Settings(BaseSettings):
    """Application settings with validation."""

    # Application
    APP_NAME: str = "Omnipath"
    APP_VERSION: str = "7.3.2"
    DEBUG: bool = False
    ENVIRONMENT: str = Field(default="production", pattern="^(development|staging|production)$")

    # LLM Provider Configuration
    # API Keys
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    XAI_API_KEY: str = ""  # Grok
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    # Model Selection (per agent type)
    COMMANDER_PROVIDER: str = "openai"
    COMMANDER_MODEL: str = "gpt-4.1-mini"
    COMMANDER_TEMPERATURE: float = 0.7

    GUARDIAN_PROVIDER: str = "openai"
    GUARDIAN_MODEL: str = "gpt-4.1-mini"
    GUARDIAN_TEMPERATURE: float = 0.3  # Lower for safety

    ARCHIVIST_PROVIDER: str = "openai"
    ARCHIVIST_MODEL: str = "gpt-4.1-mini"
    ARCHIVIST_TEMPERATURE: float = 0.5

    FORK_PROVIDER: str = "openai"
    FORK_MODEL: str = "gpt-4.1-mini"
    FORK_TEMPERATURE: float = 0.7

    # Security
    SECRET_KEY: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    JWT_SECRET_KEY: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    API_KEY_LENGTH: int = 64

    # Database
    DATABASE_URL: str = "postgresql://omnipath:omnipath@localhost:5432/omnipath"
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10

    # Redis (Event Bus & Caching)
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_MAX_CONNECTIONS: int = 50

    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_PER_HOUR: int = 1000

    # Agent System
    MAX_AGENTS_PER_TENANT: int = 100
    AGENT_EXECUTION_TIMEOUT_SECONDS: int = 300
    AGENT_MEMORY_LIMIT_MB: int = 512

    # Audit & Logging
    AUDIT_LOG_ENABLED: bool = True
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "https://nested-ai.net"]
    CORS_ALLOW_CREDENTIALS: bool = True

    # Monitoring
    PROMETHEUS_ENABLED: bool = True
    PROMETHEUS_PORT: int = 9090

    @validator("ENVIRONMENT")
    def validate_environment(cls, v):
        """Ensure environment is valid."""
        if v not in ["development", "staging", "production"]:
            raise ValueError("Invalid environment")
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"  # Ignore unknown environment variables


# Global settings instance
settings = Settings()
