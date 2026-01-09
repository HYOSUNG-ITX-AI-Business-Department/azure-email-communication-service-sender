import pytest
from pydantic import ValidationError
from app.schemas.email import (
    EmailRequest,
    EmailResponse,
    EmailStatusResponse,
    EmailStatus,
    EmailAttachment,
    QueueStatsResponse
)
from datetime import datetime, timezone


def test_email_request_minimal_valid():
    """Test minimal valid email request"""
    request = EmailRequest(
        **{
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "subject": "Test",
            "body": "Test body",
            "caller_id": "test-caller"
        }
    )
    assert request.from_address == "sender@example.com"
    assert request.to == ["recipient@example.com"]
    assert request.subject == "Test"
    assert request.body == "Test body"
    assert request.caller_id == "test-caller"
    assert request.envelope_from is None
    assert request.html is False


def test_email_request_with_envelope_from():
    """Test email request with explicit envelope_from"""
    request = EmailRequest(
        **{
            "from": "sender@example.com",
            "envelope_from": "bounce@example.com",
            "to": ["recipient@example.com"],
            "subject": "Test",
            "body": "Test body",
            "caller_id": "test-caller"
        }
    )
    assert request.envelope_from == "bounce@example.com"


def test_email_request_with_cc_bcc():
    """Test email request with CC and BCC"""
    request = EmailRequest(
        **{
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "cc": ["cc@example.com"],
            "bcc": ["bcc@example.com"],
            "subject": "Test",
            "body": "Test body",
            "caller_id": "test-caller"
        }
    )
    assert request.cc == ["cc@example.com"]
    assert request.bcc == ["bcc@example.com"]


def test_email_request_with_reply_to():
    """Test email request with reply_to"""
    request = EmailRequest(
        **{
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "reply_to": "reply@example.com",
            "subject": "Test",
            "body": "Test body",
            "caller_id": "test-caller"
        }
    )
    assert request.reply_to == "reply@example.com"


def test_email_request_html_body():
    """Test email request with HTML body"""
    request = EmailRequest(
        **{
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "subject": "Test",
            "body": "<h1>Test</h1>",
            "html": True,
            "caller_id": "test-caller"
        }
    )
    assert request.html is True
    assert request.body == "<h1>Test</h1>"


def test_email_request_with_headers():
    """Test email request with custom headers"""
    request = EmailRequest(
        **{
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "subject": "Test",
            "body": "Test body",
            "headers": {"X-Custom": "value", "X-Priority": "high"},
            "caller_id": "test-caller"
        }
    )
    assert request.headers == {"X-Custom": "value", "X-Priority": "high"}


def test_email_request_with_tags():
    """Test email request with tags"""
    request = EmailRequest(
        **{
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "subject": "Test",
            "body": "Test body",
            "tags": ["marketing", "newsletter"],
            "caller_id": "test-caller"
        }
    )
    assert request.tags == ["marketing", "newsletter"]


def test_email_request_with_idempotency_key():
    """Test email request with idempotency key"""
    request = EmailRequest(
        **{
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "subject": "Test",
            "body": "Test body",
            "idempotency_key": "unique-key-123",
            "caller_id": "test-caller"
        }
    )
    assert request.idempotency_key == "unique-key-123"


def test_email_request_with_smtp_auth_profile():
    """Test email request with SMTP auth profile ID"""
    request = EmailRequest(
        **{
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "subject": "Test",
            "body": "Test body",
            "smtp_auth_profile_id": "profile-123",
            "caller_id": "test-caller"
        }
    )
    assert request.smtp_auth_profile_id == "profile-123"


def test_email_request_invalid_from_email():
    """Test email request with invalid from email"""
    with pytest.raises(ValidationError):
        EmailRequest(
            **{
                "from": "invalid-email",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "caller_id": "test-caller"
            }
        )


def test_email_request_invalid_to_email():
    """Test email request with invalid to email"""
    with pytest.raises(ValidationError):
        EmailRequest(
            **{
                "from": "sender@example.com",
                "to": ["invalid-email"],
                "subject": "Test",
                "body": "Test body",
                "caller_id": "test-caller"
            }
        )


def test_email_request_empty_to_list():
    """Test email request with empty to list"""
    with pytest.raises(ValidationError):
        EmailRequest(
            **{
                "from": "sender@example.com",
                "to": [],
                "subject": "Test",
                "body": "Test body",
                "caller_id": "test-caller"
            }
        )


def test_email_request_missing_caller_id():
    """Test email request without caller_id"""
    with pytest.raises(ValidationError):
        EmailRequest(
            **{
                "from": "sender@example.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body"
            }
        )


def test_email_request_missing_required_fields():
    """Test email request missing required fields"""
    with pytest.raises(ValidationError):
        EmailRequest(caller_id="test-caller")


def test_email_attachment_valid():
    """Test valid email attachment"""
    attachment = EmailAttachment(
        filename="test.pdf",
        content_type="application/pdf",
        content_base64="dGVzdCBjb250ZW50"
    )
    assert attachment.filename == "test.pdf"
    assert attachment.content_type == "application/pdf"
    assert attachment.content_base64 == "dGVzdCBjb250ZW50"


def test_email_attachment_default_content_type():
    """Test email attachment with default content type"""
    attachment = EmailAttachment(
        filename="test.bin",
        content_base64="dGVzdCBjb250ZW50"
    )
    assert attachment.content_type == "application/octet-stream"


def test_email_attachment_empty_filename():
    """Test email attachment with empty filename"""
    with pytest.raises(ValidationError):
        EmailAttachment(
            filename="",
            content_base64="dGVzdCBjb250ZW50"
        )


def test_email_attachment_empty_content():
    """Test email attachment with empty content"""
    with pytest.raises(ValidationError):
        EmailAttachment(
            filename="test.pdf",
            content_base64=""
        )


def test_email_request_with_attachments():
    """Test email request with multiple attachments"""
    request = EmailRequest(
        **{
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "subject": "Test",
            "body": "Test body",
            "caller_id": "test-caller",
            "attachments": [
                {
                    "filename": "doc.pdf",
                    "content_type": "application/pdf",
                    "content_base64": "cGRmIGNvbnRlbnQ="
                },
                {
                    "filename": "image.png",
                    "content_type": "image/png",
                    "content_base64": "aW1hZ2UgY29udGVudA=="
                }
            ]
        }
    )
    assert len(request.attachments) == 2
    assert request.attachments[0].filename == "doc.pdf"
    assert request.attachments[1].filename == "image.png"


def test_email_response_valid():
    """Test valid email response"""
    now = datetime.now(timezone.utc)
    response = EmailResponse(
        email_id="test-id",
        status=EmailStatus.QUEUED,
        message="Email queued",
        created_at=now
    )
    assert response.email_id == "test-id"
    assert response.status == EmailStatus.QUEUED
    assert response.message == "Email queued"
    assert response.created_at == now


def test_email_status_response_valid():
    """Test valid email status response"""
    now = datetime.now(timezone.utc)
    response = EmailStatusResponse(
        email_id="test-id",
        status=EmailStatus.SENT,
        from_address="sender@example.com",
        envelope_from="bounce@example.com",
        to=["recipient@example.com"],
        subject="Test",
        created_at=now,
        updated_at=now,
        retry_count=1,
        error_message=None,
        sent_at=now,
        caller_id="test-caller",
        smtp_auth_profile_id="profile-123"
    )
    assert response.email_id == "test-id"
    assert response.status == EmailStatus.SENT
    assert response.retry_count == 1
    assert response.sent_at == now


def test_email_status_response_with_error():
    """Test email status response with error message"""
    now = datetime.now(timezone.utc)
    response = EmailStatusResponse(
        email_id="test-id",
        status=EmailStatus.FAILED,
        from_address="sender@example.com",
        envelope_from="sender@example.com",
        to=["recipient@example.com"],
        subject="Test",
        created_at=now,
        updated_at=now,
        retry_count=3,
        error_message="SMTP connection failed",
        sent_at=None,
        caller_id="test-caller",
        smtp_auth_profile_id=None
    )
    assert response.status == EmailStatus.FAILED
    assert response.error_message == "SMTP connection failed"
    assert response.sent_at is None


def test_queue_stats_response_valid():
    """Test valid queue stats response"""
    response = QueueStatsResponse(
        queue_size=10,
        processing_size=5,
        dlq_size=2
    )
    assert response.queue_size == 10
    assert response.processing_size == 5
    assert response.dlq_size == 2


def test_queue_stats_response_zero_values():
    """Test queue stats response with zero values"""
    response = QueueStatsResponse(
        queue_size=0,
        processing_size=0,
        dlq_size=0
    )
    assert response.queue_size == 0
    assert response.processing_size == 0
    assert response.dlq_size == 0


def test_email_status_enum_values():
    """Test EmailStatus enum has expected values"""
    assert EmailStatus.PENDING.value == "pending"
    assert EmailStatus.QUEUED.value == "queued"
    assert EmailStatus.SENDING.value == "sending"
    assert EmailStatus.SENT.value == "sent"
    assert EmailStatus.FAILED.value == "failed"
    assert EmailStatus.DLQ.value == "dlq"


def test_email_request_multiple_recipients():
    """Test email request with multiple recipients in each field"""
    request = EmailRequest(
        **{
            "from": "sender@example.com",
            "to": ["to1@example.com", "to2@example.com", "to3@example.com"],
            "cc": ["cc1@example.com", "cc2@example.com"],
            "bcc": ["bcc1@example.com", "bcc2@example.com"],
            "subject": "Test",
            "body": "Test body",
            "caller_id": "test-caller"
        }
    )
    assert len(request.to) == 3
    assert len(request.cc) == 2
    assert len(request.bcc) == 2


def test_email_request_long_subject():
    """Test email request with long subject"""
    long_subject = "A" * 1000
    request = EmailRequest(
        **{
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "subject": long_subject,
            "body": "Test body",
            "caller_id": "test-caller"
        }
    )
    assert request.subject == long_subject


def test_email_request_long_body():
    """Test email request with long body"""
    long_body = "B" * 10000
    request = EmailRequest(
        **{
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "subject": "Test",
            "body": long_body,
            "caller_id": "test-caller"
        }
    )
    assert request.body == long_body


def test_email_request_unicode_content():
    """Test email request with unicode content"""
    request = EmailRequest(
        **{
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "subject": "测试主题 🎉",
            "body": "测试正文内容 with émojis 🚀",
            "caller_id": "test-caller"
        }
    )
    assert "测试主题" in request.subject
    assert "测试正文内容" in request.body
    assert "🎉" in request.subject
    assert "🚀" in request.body
