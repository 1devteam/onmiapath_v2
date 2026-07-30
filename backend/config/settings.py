"""
Omnipath Configuration Management
Centralized configuration with environment variable support and validation.
"""

from pathlib import Path
import secrets
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEFAULT_SECRET_KEY = secrets.token_urlsafe(32)
_DEFAULT_JWT_SECRET_KEY = secrets.token_urlsafe(32)
_LOOPBACK_HOSTS = {  # nosec B104 - validation denylist, never used for binding
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
}
_PLACEHOLDER_FRAGMENTS = (
    "change_me",
    "changeme",
    "replace_me",
    "your-secret",
    "your_super_secret",
)


def _production_url_issue(name: str, value: str, allowed_schemes: tuple[str, ...]) -> str | None:
    """Return a redacted validation issue for an unsafe production service URL."""
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
    except ValueError:
        return f"{name} must be a valid service URL"

    if not any(
        scheme == allowed or scheme.startswith(f"{allowed}+") for allowed in allowed_schemes
    ):
        return f"{name} must use one of: {', '.join(allowed_schemes)}"
    if not host:
        return f"{name} must include a service hostname"
    if host.lower() in _LOOPBACK_HOSTS:
        return f"{name} must not use a loopback or wildcard hostname in production"
    return None


def _production_secret_issue(name: str, value: str, generated_default: str) -> str | None:
    """Return a redacted validation issue for an unsafe production secret."""
    lowered = value.lower()
    if value == generated_default:
        return f"{name} must be explicitly configured in production"
    if len(value) < 32:
        return f"{name} must contain at least 32 characters"
    if any(fragment in lowered for fragment in _PLACEHOLDER_FRAGMENTS):
        return f"{name} must not contain a placeholder value"
    return None


def _read_version() -> str:
    """Read version from VERSION file at project root."""
    version_file = Path(__file__).parent.parent.parent / "VERSION"
    try:
        return version_file.read_text().strip()
    except FileNotFoundError:
        return "0.0.0"


class Settings(BaseSettings):
    """Application settings with validation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Application
    APP_NAME: str = "Omnipath"
    APP_VERSION: str = _read_version()
    DEBUG: bool = False
    ENVIRONMENT: str = Field(default="development", pattern="^(development|staging|production)$")

    # LLM Provider Configuration
    # API Keys
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    XAI_API_KEY: str = ""  # Grok
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    # Model Selection (per agent type)
    COMMANDER_PROVIDER: str = "openai"
    COMMANDER_MODEL: str = "gpt-4-turbo"
    COMMANDER_TEMPERATURE: float = 0.7

    GUARDIAN_PROVIDER: str = "openai"  # Default to openai; override via GUARDIAN_PROVIDER env var
    GUARDIAN_MODEL: str = "gpt-4o-mini"  # Cost-efficient safety validator; override for claude
    GUARDIAN_TEMPERATURE: float = 0.3  # Lower for safety

    ARCHIVIST_PROVIDER: str = "google"
    ARCHIVIST_MODEL: str = "gemini-2.0-flash-exp"
    ARCHIVIST_TEMPERATURE: float = 0.5

    FORK_PROVIDER: str = "google"
    FORK_MODEL: str = "gemini-2.0-flash-exp"
    FORK_TEMPERATURE: float = 0.7

    # Security
    SECRET_KEY: str = _DEFAULT_SECRET_KEY
    JWT_SECRET_KEY: str = _DEFAULT_JWT_SECRET_KEY
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
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    CORS_ALLOW_CREDENTIALS: bool = True

    # Observability (v5.0)
    # OpenTelemetry
    OTEL_ENABLED: bool = True
    OTEL_SERVICE_NAME: str = "omnipath"
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4317"
    OTEL_EXPORTER_OTLP_INSECURE: bool = True
    OTEL_METRICS_ENABLED: bool = False

    # Prometheus Metrics
    PROMETHEUS_ENABLED: bool = True
    PROMETHEUS_PORT: int = 9090
    METRICS_ENDPOINT: str = "/metrics"

    # NATS Event Bus
    NATS_ENABLED: bool = True
    NATS_URL: str = "nats://localhost:4222"
    NATS_CLUSTER_ID: str = "omnipath-cluster"
    NATS_CLIENT_ID: str = "omnipath-backend"
    NATS_MAX_RECONNECT_ATTEMPTS: int = 10

    # Jaeger (for trace visualization)
    JAEGER_AGENT_HOST: str = "localhost"
    JAEGER_AGENT_PORT: int = 6831

    @model_validator(mode="after")
    def validate_production_configuration(self) -> "Settings":
        """Fail before startup when production endpoints or secrets are unsafe."""
        if self.ENVIRONMENT != "production":
            return self

        issues = [
            _production_secret_issue("SECRET_KEY", self.SECRET_KEY, _DEFAULT_SECRET_KEY),
            _production_secret_issue(
                "JWT_SECRET_KEY",
                self.JWT_SECRET_KEY,
                _DEFAULT_JWT_SECRET_KEY,
            ),
            _production_url_issue(
                "DATABASE_URL",
                self.DATABASE_URL,
                ("postgresql", "postgres"),
            ),
            _production_url_issue("REDIS_URL", self.REDIS_URL, ("redis", "rediss")),
        ]

        if self.NATS_ENABLED:
            issues.append(_production_url_issue("NATS_URL", self.NATS_URL, ("nats", "tls")))
        if self.OTEL_ENABLED:
            issues.append(
                _production_url_issue(
                    "OTEL_EXPORTER_OTLP_ENDPOINT",
                    self.OTEL_EXPORTER_OTLP_ENDPOINT,
                    ("http", "https", "grpc"),
                )
            )

        active_issues = [issue for issue in issues if issue]
        if active_issues:
            raise ValueError("Unsafe production configuration: " + "; ".join(active_issues))
        return self


# Global settings instance
settings = Settings()
