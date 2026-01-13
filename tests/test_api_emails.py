import pytest
from unittest.mock import AsyncMock, patch, MagicMock, ANY, call
from fastapi import status
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.schemas.email import EmailStatus
from app.services.email import IdempotencyPayloadMismatchError
from app.models.email import EmailRecord
from datetime import datetime, timezone
import json


def get_test_client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


@pytest.mark.asyncio
async def test_send_email_success():
    """Test successful email submission"""
    pending_email_record = EmailRecord(
        id="test-email-id",
        caller_id="test-caller",
        from_address="sender@yourdomain.com",
        envelope_from="sender@yourdomain.com",
        to_addresses=["recipient@example.com"],
        subject="Test Subject",
        body="Test Body",
        status=EmailStatus.PENDING.value,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        audit_log=[]
    )

    queued_email_record = EmailRecord(
        id="test-email-id",
        caller_id="test-caller",
        from_address="sender@yourdomain.com",
        envelope_from="sender@yourdomain.com",
        to_addresses=["recipient@example.com"],
        subject="Test Subject",
        body="Test Body",
        status=EmailStatus.QUEUED.value,
        retry_count=0,
        created_at=pending_email_record.created_at,
        updated_at=pending_email_record.updated_at,
        audit_log=[],
    )
    
    with patch('app.api.emails.email_service.create_email', new_callable=AsyncMock) as mock_create, \
         patch('app.api.emails.email_service.update_status', new_callable=AsyncMock) as mock_update, \
         patch('app.api.emails.queue_service.enqueue', new_callable=AsyncMock) as mock_enqueue:
        
        mock_create.return_value = pending_email_record
        mock_update.return_value = queued_email_record
        
        async with get_test_client() as client:
            response = await client.post(
                "/api/v1/emails/",
                json={
                    "from": "sender@yourdomain.com",
                    "to": ["recipient@example.com"],
                    "subject": "Test Subject",
                    "body": "Test Body",
                    "caller_id": "test-caller"
                },
                headers={"X-Caller-Id": "test-caller"}
            )
        
        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert data["email_id"] == "test-email-id"
        assert data["status"] == "queued"
        assert "message" in data
        
        mock_create.assert_awaited_once()
        mock_update.assert_awaited_once_with(ANY, "test-email-id", EmailStatus.QUEUED)
        mock_enqueue.assert_awaited_once_with("test-email-id")


@pytest.mark.asyncio
async def test_send_email_caller_id_mismatch():
    """Test email submission fails when caller_id doesn't match X-Caller-Id"""
    async with get_test_client() as client:
        response = await client.post(
            "/api/v1/emails/",
            json={
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test Subject",
                "body": "Test Body",
                "caller_id": "caller-a"
            },
            headers={"X-Caller-Id": "caller-b"}
        )
    
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "does not match" in response.json()["detail"]


@pytest.mark.asyncio
async def test_send_email_missing_caller_id_header():
    """Test email submission fails without X-Caller-Id header"""
    async with get_test_client() as client:
        response = await client.post(
            "/api/v1/emails/",
            json={
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test Subject",
                "body": "Test Body",
                "caller_id": "test-caller"
            }
        )
    
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_send_email_empty_caller_id_header():
    """Test email submission fails with empty X-Caller-Id header"""
    async with get_test_client() as client:
        response = await client.post(
            "/api/v1/emails/",
            json={
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test Subject",
                "body": "Test Body",
                "caller_id": "test-caller",
            },
            headers={"X-Caller-Id": "   "},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_send_email_idempotency_conflict():
    """Test idempotency key reuse with different payload returns conflict"""
    with patch('app.api.emails.email_service.create_email', new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = IdempotencyPayloadMismatchError("Payload mismatch")
        
        async with get_test_client() as client:
            response = await client.post(
                "/api/v1/emails/",
                json={
                    "from": "sender@yourdomain.com",
                    "to": ["recipient@example.com"],
                    "subject": "Test Subject",
                    "body": "Test Body",
                    "caller_id": "test-caller",
                    "idempotency_key": "key-123"
                },
                headers={"X-Caller-Id": "test-caller"}
            )
        
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["detail"]


@pytest.mark.asyncio
async def test_send_email_idempotency_replay_does_not_enqueue():
    """Test idempotency replay returns existing status without requeueing"""
    existing_email = EmailRecord(
        id="test-email-id",
        caller_id="test-caller",
        from_address="sender@yourdomain.com",
        envelope_from="sender@yourdomain.com",
        to_addresses=["recipient@example.com"],
        subject="Test Subject",
        body="Test Body",
        status=EmailStatus.SENT.value,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        audit_log=[],
    )

    with patch(
        "app.api.emails.email_service.create_email",
        new_callable=AsyncMock,
    ) as mock_create, patch(
        "app.api.emails.email_service.update_status",
        new_callable=AsyncMock,
    ) as mock_update, patch(
        "app.api.emails.queue_service.enqueue",
        new_callable=AsyncMock,
    ) as mock_enqueue:
        mock_create.return_value = existing_email

        async with get_test_client() as client:
            response = await client.post(
                "/api/v1/emails/",
                json={
                    "from": "sender@yourdomain.com",
                    "to": ["recipient@example.com"],
                    "subject": "Test Subject",
                    "body": "Test Body",
                    "caller_id": "test-caller",
                    "idempotency_key": "key-123",
                },
                headers={"X-Caller-Id": "test-caller"},
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["email_id"] == "test-email-id"
        assert data["status"] == EmailStatus.SENT.value
        mock_update.assert_not_awaited()
        mock_enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_email_validation_error():
    """Test validation errors return 400"""
    with patch('app.api.emails.email_service.create_email', new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = ValueError("Invalid envelope_from")
        
        async with get_test_client() as client:
            response = await client.post(
                "/api/v1/emails/",
                json={
                    "from": "sender@yourdomain.com",
                    "to": ["recipient@example.com"],
                    "subject": "Test Subject",
                    "body": "Test Body",
                    "caller_id": "test-caller"
                },
                headers={"X-Caller-Id": "test-caller"}
            )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid envelope_from" in response.json()["detail"]


@pytest.mark.asyncio
async def test_send_email_internal_error():
    """Test internal errors return 500"""
    with patch('app.api.emails.email_service.create_email', new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = Exception("Database connection failed")
        
        async with get_test_client() as client:
            response = await client.post(
                "/api/v1/emails/",
                json={
                    "from": "sender@yourdomain.com",
                    "to": ["recipient@example.com"],
                    "subject": "Test Subject",
                    "body": "Test Body",
                    "caller_id": "test-caller"
                },
                headers={"X-Caller-Id": "test-caller"}
            )
        
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


@pytest.mark.asyncio
async def test_send_email_enqueue_failure_marks_failed():
    """Test enqueue failure updates status to FAILED"""
    mock_email_record = EmailRecord(
        id="test-email-id",
        caller_id="test-caller",
        from_address="sender@yourdomain.com",
        envelope_from="sender@yourdomain.com",
        to_addresses=["recipient@example.com"],
        subject="Test Subject",
        body="Test Body",
        status=EmailStatus.PENDING.value,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        audit_log=[],
    )

    with patch(
        "app.api.emails.email_service.create_email",
        new_callable=AsyncMock,
    ) as mock_create, patch(
        "app.api.emails.email_service.update_status",
        new_callable=AsyncMock,
    ) as mock_update, patch(
        "app.api.emails.queue_service.enqueue",
        new_callable=AsyncMock,
    ) as mock_enqueue:
        mock_create.return_value = mock_email_record
        mock_update.return_value = mock_email_record
        mock_enqueue.side_effect = Exception("Redis connection failed")

        async with get_test_client() as client:
            response = await client.post(
                "/api/v1/emails/",
                json={
                    "from": "sender@yourdomain.com",
                    "to": ["recipient@example.com"],
                    "subject": "Test Subject",
                    "body": "Test Body",
                    "caller_id": "test-caller",
                },
                headers={"X-Caller-Id": "test-caller"},
            )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        mock_enqueue.assert_awaited_once_with("test-email-id")
        mock_update.assert_has_awaits(
            [
                call(ANY, "test-email-id", EmailStatus.QUEUED),
                call(
                    ANY,
                    "test-email-id",
                    EmailStatus.FAILED,
                    error_message="Failed to enqueue email for sending",
                ),
            ]
        )


@pytest.mark.asyncio
async def test_send_email_with_all_fields():
    """Test email submission with all optional fields"""
    mock_email_record = EmailRecord(
        id="test-email-id",
        caller_id="test-caller",
        from_address="sender@yourdomain.com",
        envelope_from="noreply@yourdomain.com",
        smtp_auth_profile_id="profile-123",
        reply_to="reply@yourdomain.com",
        to_addresses=["recipient@example.com"],
        cc_addresses=["cc@example.com"],
        bcc_addresses=["bcc@example.com"],
        subject="Test Subject",
        body="<h1>Test</h1>",
        is_html=1,
        headers={"X-Custom": "value"},
        tags=["tag1", "tag2"],
        status=EmailStatus.PENDING.value,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        audit_log=[]
    )
    
    with patch('app.api.emails.email_service.create_email', new_callable=AsyncMock) as mock_create, \
         patch('app.api.emails.email_service.update_status', new_callable=AsyncMock) as mock_update, \
         patch('app.api.emails.queue_service.enqueue', new_callable=AsyncMock):
        
        mock_create.return_value = mock_email_record
        mock_update.return_value = mock_email_record
        
        async with get_test_client() as client:
            response = await client.post(
                "/api/v1/emails/",
                json={
                    "from": "sender@yourdomain.com",
                    "envelope_from": "noreply@yourdomain.com",
                    "to": ["recipient@example.com"],
                    "cc": ["cc@example.com"],
                    "bcc": ["bcc@example.com"],
                    "reply_to": "reply@yourdomain.com",
                    "subject": "Test Subject",
                    "body": "<h1>Test</h1>",
                    "html": True,
                    "headers": {"X-Custom": "value"},
                    "tags": ["tag1", "tag2"],
                    "caller_id": "test-caller",
                    "smtp_auth_profile_id": "profile-123",
                    "idempotency_key": "key-456"
                },
                headers={"X-Caller-Id": "test-caller"}
            )
        
        assert response.status_code == status.HTTP_202_ACCEPTED


@pytest.mark.asyncio
async def test_get_email_status_success():
    """Test successful email status retrieval"""
    mock_email = EmailRecord(
        id="test-email-id",
        caller_id="test-caller",
        from_address="sender@yourdomain.com",
        envelope_from="sender@yourdomain.com",
        to_addresses=["recipient@example.com"],
        subject="Test Subject",
        body="Test Body",
        status=EmailStatus.SENT.value,
        retry_count=1,
        error_message=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        sent_at=datetime.now(timezone.utc),
        smtp_auth_profile_id=None,
        is_html=0
    )
    
    with patch('app.api.emails.email_service.get_by_id', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_email
        
        async with get_test_client() as client:
            response = await client.get(
                "/api/v1/emails/test-email-id",
                headers={"X-Caller-Id": "test-caller"},
            )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["email_id"] == "test-email-id"
        assert data["status"] == "sent"
        assert data["from_address"] == "sender@yourdomain.com"
        assert data["retry_count"] == 1


@pytest.mark.asyncio
async def test_get_email_status_unknown_status_falls_back_to_failed():
    """Test email status handles unknown status values gracefully"""
    mock_email = EmailRecord(
        id="test-email-id",
        caller_id="test-caller",
        from_address="sender@yourdomain.com",
        envelope_from="sender@yourdomain.com",
        to_addresses=["recipient@example.com"],
        subject="Test Subject",
        body="Test Body",
        status="unknown-status",
        retry_count=1,
        error_message=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        sent_at=datetime.now(timezone.utc),
        smtp_auth_profile_id=None,
        is_html=0,
    )

    with patch("app.api.emails.email_service.get_by_id", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_email

        async with get_test_client() as client:
            response = await client.get(
                "/api/v1/emails/test-email-id",
                headers={"X-Caller-Id": "test-caller"},
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == EmailStatus.FAILED.value


@pytest.mark.asyncio
async def test_get_email_status_caller_id_mismatch():
    """Test email status retrieval is caller-scoped"""
    mock_email = EmailRecord(
        id="test-email-id",
        caller_id="caller-a",
        from_address="sender@yourdomain.com",
        envelope_from="sender@yourdomain.com",
        to_addresses=["recipient@example.com"],
        subject="Test Subject",
        body="Test Body",
        status=EmailStatus.SENT.value,
        retry_count=1,
        error_message=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        sent_at=datetime.now(timezone.utc),
        smtp_auth_profile_id=None,
        is_html=0,
    )

    with patch('app.api.emails.email_service.get_by_id', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_email

        async with get_test_client() as client:
            response = await client.get(
                "/api/v1/emails/test-email-id",
                headers={"X-Caller-Id": "caller-b"},
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_email_status_not_found():
    """Test email status retrieval for non-existent email"""
    with patch('app.api.emails.email_service.get_by_id', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        
        async with get_test_client() as client:
            response = await client.get(
                "/api/v1/emails/nonexistent-id",
                headers={"X-Caller-Id": "test-caller"},
            )
        
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_email_status_with_json_string_addresses():
    """Test email status handles JSON string for to_addresses"""
    mock_email = EmailRecord(
        id="test-email-id",
        caller_id="test-caller",
        from_address="sender@yourdomain.com",
        envelope_from="sender@yourdomain.com",
        to_addresses='["recipient@example.com"]',  # JSON string
        subject="Test Subject",
        body="Test Body",
        status=EmailStatus.SENT.value,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        sent_at=datetime.now(timezone.utc),
        smtp_auth_profile_id=None,
        is_html=0
    )
    
    with patch('app.api.emails.email_service.get_by_id', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_email
        
        async with get_test_client() as client:
            response = await client.get(
                "/api/v1/emails/test-email-id",
                headers={"X-Caller-Id": "test-caller"},
            )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["to"] == ["recipient@example.com"]


@pytest.mark.asyncio
async def test_get_email_status_with_json_string_single_address():
    """Test email status handles JSON string scalar for to_addresses"""
    mock_email = EmailRecord(
        id="test-email-id",
        caller_id="test-caller",
        from_address="sender@yourdomain.com",
        envelope_from="sender@yourdomain.com",
        to_addresses='"recipient@example.com"',  # JSON string scalar
        subject="Test Subject",
        body="Test Body",
        status=EmailStatus.SENT.value,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        sent_at=datetime.now(timezone.utc),
        smtp_auth_profile_id=None,
        is_html=0,
    )

    with patch("app.api.emails.email_service.get_by_id", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_email

        async with get_test_client() as client:
            response = await client.get(
                "/api/v1/emails/test-email-id",
                headers={"X-Caller-Id": "test-caller"},
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["to"] == ["recipient@example.com"]


@pytest.mark.asyncio
async def test_get_email_status_with_non_list_json_addresses():
    """Test email status handles non-list JSON for to_addresses"""
    mock_email = EmailRecord(
        id="test-email-id",
        caller_id="test-caller",
        from_address="sender@yourdomain.com",
        envelope_from="sender@yourdomain.com",
        to_addresses='{"recipient":"example@example.com"}',  # JSON object
        subject="Test Subject",
        body="Test Body",
        status=EmailStatus.SENT.value,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        sent_at=datetime.now(timezone.utc),
        smtp_auth_profile_id=None,
        is_html=0,
    )

    with patch("app.api.emails.email_service.get_by_id", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_email

        async with get_test_client() as client:
            response = await client.get(
                "/api/v1/emails/test-email-id",
                headers={"X-Caller-Id": "test-caller"},
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["to"] == []


@pytest.mark.asyncio
async def test_get_email_status_with_corrupted_addresses():
    """Test email status handles corrupted to_addresses gracefully"""
    mock_email = EmailRecord(
        id="test-email-id",
        caller_id="test-caller",
        from_address="sender@yourdomain.com",
        envelope_from="sender@yourdomain.com",
        to_addresses='not-valid-json',  # Corrupted JSON
        subject="Test Subject",
        body="Test Body",
        status=EmailStatus.SENT.value,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        sent_at=datetime.now(timezone.utc),
        smtp_auth_profile_id=None,
        is_html=0
    )
    
    with patch('app.api.emails.email_service.get_by_id', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_email
        
        async with get_test_client() as client:
            response = await client.get(
                "/api/v1/emails/test-email-id",
                headers={"X-Caller-Id": "test-caller"},
            )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["to"] == []  # Falls back to empty list


@pytest.mark.asyncio
async def test_get_queue_stats_success():
    """Test successful queue stats retrieval"""
    with patch('app.api.emails.queue_service.get_queue_size', new_callable=AsyncMock) as mock_queue, \
         patch('app.api.emails.queue_service.get_processing_size', new_callable=AsyncMock) as mock_processing, \
         patch('app.api.emails.queue_service.get_delayed_size', new_callable=AsyncMock) as mock_delayed, \
         patch('app.api.emails.queue_service.get_dlq_size', new_callable=AsyncMock) as mock_dlq:
        
        mock_queue.return_value = 10
        mock_processing.return_value = 5
        mock_delayed.return_value = 3
        mock_dlq.return_value = 2
        
        async with get_test_client() as client:
            response = await client.get(
                "/api/v1/emails/",
                headers={"X-Caller-Id": "test-caller"},
            )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["queue_size"] == 10
        assert data["processing_size"] == 5
        assert data["delayed_size"] == 3
        assert data["dlq_size"] == 2


@pytest.mark.asyncio
async def test_get_queue_stats_error():
    """Test queue stats handles errors gracefully"""
    with patch('app.api.emails.queue_service.get_queue_size', new_callable=AsyncMock) as mock_queue:
        mock_queue.side_effect = Exception("Redis connection failed")
        
        async with get_test_client() as client:
            response = await client.get(
                "/api/v1/emails/",
                headers={"X-Caller-Id": "test-caller"},
            )
        
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


@pytest.mark.asyncio
async def test_root_endpoint():
    """Test root endpoint returns service info"""
    async with get_test_client() as client:
        response = await client.get("/")
    
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "service" in data
    assert "version" in data
    assert "status" in data


@pytest.mark.asyncio
async def test_health_check_endpoint():
    """Test health check endpoint"""

    class FakeSession:
        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, _exc_type, _exc, _tb) -> bool:
            return False

        async def execute(self, _stmt) -> None:
            return None

    def fake_session_local() -> FakeSession:
        return FakeSession()

    fake_redis_client = MagicMock(ping=AsyncMock(return_value=True))

    with patch("app.main.AsyncSessionLocal", new=fake_session_local), patch(
        "app.main.queue_service.redis_client",
        new=fake_redis_client,
    ):
        async with get_test_client() as client:
            response = await client.get("/health")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "healthy"
    assert data["checks"] == {"redis": True, "database": True}


@pytest.mark.asyncio
async def test_readiness_check_endpoint():
    """Test readiness check endpoint"""

    class FakeSession:
        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, _exc_type, _exc, _tb) -> bool:
            return False

        async def execute(self, _stmt) -> None:
            return None

    def fake_session_local() -> FakeSession:
        return FakeSession()

    fake_redis_client = MagicMock(ping=AsyncMock(return_value=True))

    with patch("app.main.AsyncSessionLocal", new=fake_session_local), patch(
        "app.main.queue_service.redis_client",
        new=fake_redis_client,
    ):
        async with get_test_client() as client:
            response = await client.get("/ready")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "ready"
    assert data["checks"] == {"redis": True, "database": True}


@pytest.mark.asyncio
async def test_send_email_with_attachments():
    """Test email submission with attachments"""
    mock_email_record = EmailRecord(
        id="test-email-id",
        caller_id="test-caller",
        from_address="sender@yourdomain.com",
        envelope_from="sender@yourdomain.com",
        to_addresses=["recipient@example.com"],
        subject="Test with Attachments",
        body="Test Body",
        attachments=[{
            "filename": "test.pdf",
            "content_type": "application/pdf",
            "content_base64": "dGVzdA=="
        }],
        status=EmailStatus.PENDING.value,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        audit_log=[]
    )
    
    with patch('app.api.emails.email_service.create_email', new_callable=AsyncMock) as mock_create, \
         patch('app.api.emails.email_service.update_status', new_callable=AsyncMock) as mock_update, \
         patch('app.api.emails.queue_service.enqueue', new_callable=AsyncMock):
        
        mock_create.return_value = mock_email_record
        mock_update.return_value = mock_email_record
        
        async with get_test_client() as client:
            response = await client.post(
                "/api/v1/emails/",
                json={
                    "from": "sender@yourdomain.com",
                    "to": ["recipient@example.com"],
                    "subject": "Test with Attachments",
                    "body": "Test Body",
                    "caller_id": "test-caller",
                    "attachments": [{
                        "filename": "test.pdf",
                        "content_type": "application/pdf",
                        "content_base64": "dGVzdA=="
                    }]
                },
                headers={"X-Caller-Id": "test-caller"}
            )
        
        assert response.status_code == status.HTTP_202_ACCEPTED