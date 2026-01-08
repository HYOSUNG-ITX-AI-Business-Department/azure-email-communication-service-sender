from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # SMTP Configuration
    smtp_host: str = "smtp.azurecomm.net"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    
    # Allowed MailFrom addresses
    allowed_mailfrom: str = ""
    
    # Redis Configuration
    redis_url: str = "redis://localhost:6379/0"
    
    # Database Configuration
    database_url: str = "postgresql+asyncpg://emailuser:CHANGE_ME@localhost:5432/emails"
    
    # Retry Configuration
    max_retries: int = 3
    retry_delay_seconds: int = 60
    
    # API Configuration
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        env_file_encoding = 'utf-8'
    
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


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


# Global settings instance for convenience
settings = get_settings()
