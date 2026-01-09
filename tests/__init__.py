import pytest
import os


@pytest.fixture(scope="session", autouse=True)
def setup_test_env():
    """Set up test environment variables"""
    original_env = {
        key: os.environ.get(key)
        for key in [
            "SMTP_USERNAME",
            "SMTP_PASSWORD",
            "ALLOWED_MAILFROM",
            "REDIS_URL",
            "DATABASE_URL",
        ]
    }

    os.environ["SMTP_USERNAME"] = "test@example.com"
    os.environ["SMTP_PASSWORD"] = "testpassword"
    os.environ["ALLOWED_MAILFROM"] = "sender@yourdomain.com,noreply@yourdomain.com"
    os.environ["REDIS_URL"] = "redis://localhost:6379/1"
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

    yield

    for key, value in original_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
