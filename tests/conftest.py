import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.models.email import Base
from app.config import Settings
from app.services.email import EmailService
from app.schemas.email import EmailRequest


@pytest.fixture
def test_settings():
    """Override settings for testing"""
    return Settings(
        smtp_host="smtp.azurecomm.net",
        smtp_port=587,
        smtp_username="test@example.com",
        smtp_password="testpass",
        allowed_mailfrom="sender@yourdomain.com,noreply@yourdomain.com",
        redis_url="redis://localhost:6379/1",
        database_url="sqlite+aiosqlite:///:memory:",
        max_retries=3,
        retry_delay_seconds=1
    )


@pytest.fixture
async def db_session():
    """Create test database session"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False
    )
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    AsyncSessionLocal = sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False
    )
    
    async with AsyncSessionLocal() as session:
        yield session
    
    await engine.dispose()


@pytest.fixture
def email_service():
    """Create email service instance"""
    return EmailService()
