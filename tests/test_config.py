import pytest
from unittest.mock import patch
from app.config import Settings, get_settings


def test_get_allowed_mailfrom_list_success():
    """Test parsing valid comma-separated MailFrom addresses"""
    settings = Settings(allowed_mailfrom="Addr1@Example.com,addr2@EXAMPLE.com")
    addresses = settings.get_allowed_mailfrom_list()
    assert addresses == ["Addr1@example.com", "addr2@example.com"]


def test_get_allowed_mailfrom_list_with_whitespace():
    """Test parsing MailFrom addresses with whitespace"""
    settings = Settings(allowed_mailfrom=" Addr1@Example.com , addr2@EXAMPLE.com ")
    addresses = settings.get_allowed_mailfrom_list()
    assert addresses == ["Addr1@example.com", "addr2@example.com"]


def test_get_allowed_mailfrom_list_single_address():
    """Test parsing single MailFrom address"""
    settings = Settings(allowed_mailfrom="Addr1@Example.com")
    addresses = settings.get_allowed_mailfrom_list()
    assert addresses == ["Addr1@example.com"]


def test_get_allowed_mailfrom_list_empty_raises_error():
    """Test empty MailFrom configuration raises error"""
    settings = Settings(allowed_mailfrom="")
    with pytest.raises(ValueError, match="required and cannot be empty"):
        settings.get_allowed_mailfrom_list()


def test_get_allowed_mailfrom_list_whitespace_only_raises_error():
    """Test whitespace-only MailFrom configuration raises error"""
    settings = Settings(allowed_mailfrom="   ")
    with pytest.raises(ValueError, match="required and cannot be empty"):
        settings.get_allowed_mailfrom_list()


def test_get_allowed_mailfrom_list_with_empty_entries():
    """Test MailFrom list filters out empty entries"""
    settings = Settings(allowed_mailfrom="Addr1@Example.com,,addr2@EXAMPLE.com")
    addresses = settings.get_allowed_mailfrom_list()
    assert addresses == ["Addr1@example.com", "addr2@example.com"]


def test_get_allowed_mailfrom_list_all_empty_entries_raises_error():
    """Test all empty entries raises error"""
    settings = Settings(allowed_mailfrom=",,,")
    with pytest.raises(ValueError, match="at least one valid email address"):
        settings.get_allowed_mailfrom_list()


def test_get_allowed_mailfrom_list_invalid_entry_raises_error():
    settings = Settings(allowed_mailfrom="not-an-email")
    with pytest.raises(ValueError, match="must be in the form local@domain"):
        settings.get_allowed_mailfrom_list()


def test_get_allowed_headers_list_success():
    """Test parsing valid comma-separated headers"""
    settings = Settings(allowed_headers="X-Custom-1,X-Custom-2")
    headers = settings.get_allowed_headers_list()
    assert headers == ["X-Custom-1", "X-Custom-2"]


def test_get_allowed_headers_list_with_whitespace():
    """Test parsing headers with whitespace"""
    settings = Settings(allowed_headers=" X-Custom-1 , X-Custom-2 ")
    headers = settings.get_allowed_headers_list()
    assert headers == ["X-Custom-1", "X-Custom-2"]


def test_get_allowed_headers_list_empty_raises_error():
    """Test empty headers configuration raises error"""
    settings = Settings(allowed_headers="")
    with pytest.raises(ValueError, match="required when headers are provided"):
        settings.get_allowed_headers_list()


def test_get_allowed_headers_list_whitespace_only_raises_error():
    """Test whitespace-only headers configuration raises error"""
    settings = Settings(allowed_headers="   ")
    with pytest.raises(ValueError, match="required when headers are provided"):
        settings.get_allowed_headers_list()


def test_get_allowed_headers_list_with_empty_entries():
    """Test headers list filters out empty entries"""
    settings = Settings(allowed_headers="X-Custom-1,,X-Custom-2")
    headers = settings.get_allowed_headers_list()
    assert headers == ["X-Custom-1", "X-Custom-2"]


def test_get_allowed_headers_list_all_empty_entries_raises_error():
    """Test all empty entries raises error"""
    settings = Settings(allowed_headers=",,,")
    with pytest.raises(ValueError, match="at least one header name"):
        settings.get_allowed_headers_list()


def test_settings_default_values():
    """Test default settings values"""
    settings = Settings(allowed_mailfrom="test@example.com")
    assert settings.smtp_host == "smtp.azurecomm.net"
    assert settings.smtp_port == 587
    assert settings.max_retries == 3
    assert settings.retry_delay_seconds == 60
    assert settings.max_retry_delay_seconds == 0
    assert settings.retry_delay_jitter_seconds == 0
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000


def test_settings_custom_values():
    """Test custom settings values"""
    settings = Settings(
        smtp_host="custom.smtp.com",
        smtp_port=465,
        smtp_username="custom@user.com",
        smtp_password="custompass",
        allowed_mailfrom="test@example.com",
        redis_url="redis://custom:6379/0",
        database_url="postgresql://custom/db",
        max_retries=5,
        retry_delay_seconds=120,
        max_retry_delay_seconds=300,
        retry_delay_jitter_seconds=5,
        api_host="0.0.0.0",
        api_port=9000
    )
    assert settings.smtp_host == "custom.smtp.com"
    assert settings.smtp_port == 465
    assert settings.smtp_username == "custom@user.com"
    assert settings.smtp_password == "custompass"
    assert settings.redis_url == "redis://custom:6379/0"
    assert settings.database_url == "postgresql://custom/db"
    assert settings.max_retries == 5
    assert settings.retry_delay_seconds == 120
    assert settings.max_retry_delay_seconds == 300
    assert settings.retry_delay_jitter_seconds == 5
    assert settings.api_host == "0.0.0.0"
    assert settings.api_port == 9000


def test_get_settings_caching():
    """Test get_settings returns cached instance"""
    settings1 = get_settings()
    settings2 = get_settings()
    assert settings1 is settings2


def test_settings_case_insensitive():
    """Test settings are case-insensitive"""
    with patch.dict('os.environ', {
        'SMTP_HOST': 'test.smtp.com',
        'smtp_port': '465',
        'ALLOWED_MAILFROM': 'test@example.com'
    }):
        settings = Settings()
        assert settings.smtp_host == 'test.smtp.com'
        assert settings.smtp_port == 465


def test_settings_from_env_file():
    """Test settings can be loaded from environment variables"""
    with patch.dict('os.environ', {
        'SMTP_HOST': 'env.smtp.com',
        'SMTP_PORT': '2525',
        'SMTP_USERNAME': 'envuser',
        'SMTP_PASSWORD': 'envpass',
        'ALLOWED_MAILFROM': 'env@example.com',
        'REDIS_URL': 'redis://envhost:6379/0',
        'DATABASE_URL': 'postgresql://envdb',
        'MAX_RETRIES': '10',
        'RETRY_DELAY_SECONDS': '300',
        'MAX_RETRY_DELAY_SECONDS': '600',
        'RETRY_DELAY_JITTER_SECONDS': '7',
        'API_HOST': '0.0.0.0',
        'API_PORT': '8080'
    }):
        settings = Settings()
        assert settings.smtp_host == 'env.smtp.com'
        assert settings.smtp_port == 2525
        assert settings.smtp_username == 'envuser'
        assert settings.smtp_password == 'envpass'
        assert settings.redis_url == 'redis://envhost:6379/0'
        assert settings.database_url == 'postgresql://envdb'
        assert settings.max_retries == 10
        assert settings.retry_delay_seconds == 300
        assert settings.max_retry_delay_seconds == 600
        assert settings.retry_delay_jitter_seconds == 7
        assert settings.api_host == '0.0.0.0'
        assert settings.api_port == 8080
