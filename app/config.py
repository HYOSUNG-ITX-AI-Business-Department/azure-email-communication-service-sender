from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class ConfigurationError(ValueError):
    """Configuration validation error."""

    ALLOWED_MAILFROM_EMPTY = "ALLOWED_MAILFROM configuration is required and cannot be empty"
    ALLOWED_MAILFROM_NO_VALID_ADDRESS = (
        "ALLOWED_MAILFROM must contain at least one valid email address"
    )
    ALLOWED_MAILFROM_INVALID_FORMAT = (
        "ALLOWED_MAILFROM entries must be in the form local@domain"
    )
    ALLOWED_HEADERS_EMPTY = (
        "ALLOWED_HEADERS configuration is required when headers are provided"
    )
    ALLOWED_HEADERS_NO_VALID_HEADER = "ALLOWED_HEADERS must contain at least one header name"


class Settings(BaseSettings):
    # SMTP Configuration
    smtp_host: str = "smtp.azurecomm.net"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    
    # Allowed MailFrom addresses
    allowed_mailfrom: str = ""

    # Allowed custom headers (comma-separated)
    allowed_headers: str = ""
    
    # Redis Configuration
    redis_url: str = "redis://localhost:6379/0"
    
    # Database Configuration
    database_url: str = "postgresql+asyncpg://emailuser@localhost:5432/emails"
    
    # Retry Configuration
    max_retries: int = 3
    retry_delay_seconds: int = 60
    max_retry_delay_seconds: int = 0
    retry_delay_jitter_seconds: int = 0
    
    # API Configuration
    # Note: Production deployments should explicitly set API_HOST=0.0.0.0 via environment variable
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    debug: bool = False

    # Metrics (Prometheus)
    metrics_enabled: bool = False
    metrics_path: str = "/metrics"
    worker_metrics_host: str = "127.0.0.1"
    worker_metrics_port: int = 8001
    worker_metrics_poll_interval_seconds: int = 15

    # Sweeper (DB/queue reconciliation)
    sweeper_enabled: bool = False
    sweeper_interval_seconds: int = 60
    sweeper_grace_seconds: int = 60
    sweeper_batch_size: int = 100
    sweeper_max_requeue_attempts: int = 10

    # Admin/ops allowlist for queue stats endpoint (comma-separated caller ids)
    queue_stats_allowed_callers: str = ""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        env_file_encoding="utf-8",
    )
    
    def get_allowed_mailfrom_list(self) -> list[str]:
        """Parse comma-separated allowed MailFrom addresses.

        Normalizes only the domain part to lowercase and preserves the local-part
        (RFC 5321 section 2.3.11).
        """
        if not self.allowed_mailfrom or not self.allowed_mailfrom.strip():
            raise ConfigurationError(
                ConfigurationError.ALLOWED_MAILFROM_EMPTY
            )
        
        # Split, strip whitespace, and filter out empty/whitespace-only strings
        addresses = [addr.strip() for addr in self.allowed_mailfrom.split(",")]
        addresses = [addr for addr in addresses if addr]  # Filter out empty strings
        
        if not addresses:
            raise ConfigurationError(
                ConfigurationError.ALLOWED_MAILFROM_NO_VALID_ADDRESS
            )

        normalized: list[str] = []
        for address in addresses:
            local_part, separator, domain_part = address.rpartition("@")
            local_part = local_part.strip()
            domain_part = domain_part.strip()
            if separator != "@" or not local_part or not domain_part:
                raise ConfigurationError(
                    ConfigurationError.ALLOWED_MAILFROM_INVALID_FORMAT
                )
            normalized.append(f"{local_part}@{domain_part.lower()}")

        return normalized

    def get_allowed_headers_list(self) -> list[str]:
        """Parse comma-separated allowed custom headers"""
        if not self.allowed_headers or not self.allowed_headers.strip():
            raise ConfigurationError(
                ConfigurationError.ALLOWED_HEADERS_EMPTY
            )

        headers = [header.strip() for header in self.allowed_headers.split(",")]
        headers = [header for header in headers if header]

        if not headers:
            raise ConfigurationError(
                ConfigurationError.ALLOWED_HEADERS_NO_VALID_HEADER
            )

        return headers

    def get_queue_stats_allowed_callers_list(self) -> list[str]:
        """Parse comma-separated queue stats allowlist."""
        if not self.queue_stats_allowed_callers or not self.queue_stats_allowed_callers.strip():
            return []

        callers = [caller.strip() for caller in self.queue_stats_allowed_callers.split(",")]
        return [caller for caller in callers if caller]


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


class _SettingsProxy:
    def __getattr__(self, name: str) -> object:
        return getattr(get_settings(), name)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Settings are read-only; use environment variables")

    def __repr__(self) -> str:
        return repr(get_settings())


settings = _SettingsProxy()