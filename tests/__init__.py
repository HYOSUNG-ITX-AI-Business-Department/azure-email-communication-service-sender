import pytest
import os


@pytest.fixture(scope="session", autouse=True)
def setup_test_env():
    """Set up test environment variables"""
    os.environ["SMTP_USERNAME"] = "test@example.com"
    os.environ["SMTP_PASSWORD"] = "testpassword"
    os.environ["ALLOWED_MAILFROM"] = "sender@yourdomain.com,noreply@yourdomain.com"
    os.environ["REDIS_URL"] = "redis://localhost:6379/1"
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
