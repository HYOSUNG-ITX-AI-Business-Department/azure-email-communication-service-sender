import pytest
import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models.email import Base


@pytest.fixture(scope="session", autouse=True)
def setup_queue_stats_env():
    """Set up test environment variables for queue stats access."""
    original_value = os.environ.get("QUEUE_STATS_ALLOWED_CALLERS")
    os.environ["QUEUE_STATS_ALLOWED_CALLERS"] = "test-caller"

    from app.config import get_settings

    get_settings.cache_clear()
    yield

    if original_value is None:
        os.environ.pop("QUEUE_STATS_ALLOWED_CALLERS", None)
    else:
        os.environ["QUEUE_STATS_ALLOWED_CALLERS"] = original_value

    get_settings.cache_clear()


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
        await conn.commit()

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
