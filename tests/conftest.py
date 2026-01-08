import pytest
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.models.email import Base


@pytest.fixture
async def db_session():
    """Create test database session
    
    Uses PostgreSQL test database if TEST_DATABASE_URL is set,
    otherwise falls back to SQLite in-memory for quick local testing.
    """
    test_db_url = os.getenv(
        "TEST_DATABASE_URL",
        "sqlite+aiosqlite:///:memory:"  # Fallback for local testing
    )
    
    engine = create_async_engine(
        test_db_url,
        echo=False
    )
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    AsyncSessionLocal = sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False
    )
    
    session = AsyncSessionLocal()
    try:
        yield session
    finally:
        await session.close()
        # Clean up tables for PostgreSQL tests
        if "postgresql" in test_db_url:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()
