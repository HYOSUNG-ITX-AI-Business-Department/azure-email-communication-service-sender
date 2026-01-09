import pytest
import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
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
    
    async with engine.connect() as conn:
        await conn.run_sync(Base.metadata.create_all)

        trans = await conn.begin()
        AsyncSessionLocal = async_sessionmaker(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        session = AsyncSessionLocal()
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()

    await engine.dispose()
