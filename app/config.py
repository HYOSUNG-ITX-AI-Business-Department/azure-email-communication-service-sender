from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


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
    database_url: str = "postgresql+asyncpg://emailuser:CHANGE_ME@localhost:5432/emails"
    
    # Retry Configuration
    max_retries: int = 3
    retry_delay_seconds: int = 60
    
    # API Configuration
    # Note: Production deployments should explicitly set API_HOST=0.0.0.0 via environment variable
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        env_file_encoding="utf-8",
    )
    
    def get_allowed_mailfrom_list(self) -> list[str]:
        """Parse comma-separated allowed MailFrom addresses"""
        if not self.allowed_mailfrom or not self.allowed_mailfrom.strip():
            raise ValueError("ALLOWED_MAILFROM configuration is required and cannot be empty")
        
        # Split, strip whitespace, and filter out empty/whitespace-only strings
        addresses = [addr.strip() for addr in self.allowed_mailfrom.split(",")]
        addresses = [addr for addr in addresses if addr]  # Filter out empty strings
        
        if not addresses:
            raise ValueError("ALLOWED_MAILFROM must contain at least one valid email address")
        
        return addresses

    def get_allowed_headers_list(self) -> list[str]:
        """Parse comma-separated allowed custom headers"""
        if not self.allowed_headers or not self.allowed_headers.strip():
            raise ValueError(
                "ALLOWED_HEADERS configuration is required when headers are provided"
            )

        headers = [header.strip() for header in self.allowed_headers.split(",")]
        headers = [header for header in headers if header]

        if not headers:
            raise ValueError("ALLOWED_HEADERS must contain at least one header name")

        return headers


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


# Global settings instance for convenience
settings = get_settings()
