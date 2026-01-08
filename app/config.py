from pydantic_settings import BaseSettings
from typing import List, Optional
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
    database_url: str = "sqlite+aiosqlite:///./emails.db"
    
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
    
    def get_allowed_mailfrom_list(self) -> List[str]:
        """Parse comma-separated allowed MailFrom addresses"""
        if not self.allowed_mailfrom:
            return []
        return [addr.strip() for addr in self.allowed_mailfrom.split(",")]


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


# Global settings instance for convenience
settings = get_settings()
