import json
import pytest
from unittest.mock import patch
from pydantic import ValidationError
from app.services.email import (
    EmailService,
    IdempotencyPayloadMismatchError,
    StoredPayloadParseError,
)
from app.schemas.email import EmailRequest, EmailStatus


@pytest.mark.asyncio
async def test_create_email_with_default_envelope_from(db_session):
    """Test email creation with default envelope_from (aligned)"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com",
            "noreply@yourdomain.com"
        ]
        
        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "caller_id": "service-a"
            }
        )
        
        email = await email_service.create_email(db_session, request)
        
        assert email.from_address == "sender@yourdomain.com"
        assert email.envelope_from == "sender@yourdomain.com"  # Default alignment
        assert email.status == EmailStatus.PENDING


@pytest.mark.asyncio
async def test_create_email_with_explicit_envelope_from(db_session):
    """Test email creation with explicit envelope_from"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com",
            "noreply@yourdomain.com"
        ]
        
        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "envelope_from": "noreply@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "caller_id": "service-a"
            }
        )
        
        email = await email_service.create_email(db_session, request)
        
        assert email.from_address == "sender@yourdomain.com"
        assert email.envelope_from == "noreply@yourdomain.com"


@pytest.mark.asyncio
async def test_create_email_with_mixed_case_envelope_from(db_session):
    """Test envelope_from validation is case-insensitive"""
    email_service = EmailService()

    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]

        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "envelope_from": "Sender@YourDomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "caller_id": "service-a"
            }
        )

        email = await email_service.create_email(db_session, request)

        # Validation is case-insensitive, but stored value preserves request casing
        assert email.envelope_from == request.envelope_from


@pytest.mark.asyncio
async def test_create_email_with_invalid_envelope_from(db_session):
    """Test email creation fails with invalid envelope_from"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]
        
        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "envelope_from": "notallowed@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "caller_id": "service-a"
            }
        )
        
        with pytest.raises(ValueError, match="not in allowed MailFrom list"):
            await email_service.create_email(db_session, request)


@pytest.mark.asyncio
async def test_idempotency_key(db_session):
    """Test idempotency prevents duplicate submissions per caller"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]
        
        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "idempotency_key": "unique-key-123",
                "caller_id": "service-a"
            }
        )
        
        # First submission
        email1 = await email_service.create_email(db_session, request)
        
        # Second submission with same key and caller
        email2 = await email_service.create_email(db_session, request)
        
        # Should return the same email
        assert email1.id == email2.id
        
        # Different caller with same key should create new email
        request_different_caller = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "idempotency_key": "unique-key-123",
                "caller_id": "service-b"
            }
        )
        
        email3 = await email_service.create_email(db_session, request_different_caller)
        
        # Should create a new email for different caller
        assert email3.id != email1.id
        assert email3.caller_id == "service-b"


def test_caller_id_is_required():
    """Test caller_id is required for email requests"""
    with pytest.raises(ValidationError):
        EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "idempotency_key": "key-without-caller"
            }
        )


def test_reject_crlf_in_subject():
    """Test CR/LF characters are rejected in subject"""
    with pytest.raises(ValidationError):
        EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test\r\nBcc: attacker@example.com",
                "body": "Test body",
                "caller_id": "service-a",
            }
        )


def test_reject_crlf_in_headers():
    """Test CR/LF characters are rejected in headers"""
    with pytest.raises(ValidationError):
        EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "caller_id": "service-a",
                "headers": {"X-Custom": "value\r\nBcc: attacker@example.com"},
            }
        )


@pytest.mark.asyncio
async def test_idempotency_not_enforced_without_idempotency_key(db_session):
    """Test idempotency is not enforced when idempotency_key is missing"""
    email_service = EmailService()

    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]

        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "caller_id": "service-a"
            }
        )

        email1 = await email_service.create_email(db_session, request)
        email2 = await email_service.create_email(db_session, request)

        assert email1.id != email2.id


@pytest.mark.asyncio
async def test_idempotency_key_payload_mismatch(db_session):
    """Test idempotency key reuse with different payload raises conflict"""
    email_service = EmailService()

    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]

        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "idempotency_key": "unique-key-123",
                "caller_id": "service-a"
            }
        )

        await email_service.create_email(db_session, request)

        request_modified = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Changed",
                "body": "Test body",
                "idempotency_key": "unique-key-123",
                "caller_id": "service-a"
            }
        )

        with pytest.raises(IdempotencyPayloadMismatchError):
            await email_service.create_email(db_session, request_modified)


@pytest.mark.asyncio
async def test_update_status_with_audit_trail(db_session):
    """Test status update creates audit trail"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]
        
        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "caller_id": "service-a"
            }
        )
        
        email = await email_service.create_email(db_session, request)
        
        # Update status
        updated = await email_service.update_status(
            db_session,
            email.id,
            EmailStatus.QUEUED
        )
        
        assert updated.status == EmailStatus.QUEUED
        assert updated.audit_log is not None

        audit_entries = updated.audit_log
        if isinstance(audit_entries, str):
            audit_entries = json.loads(audit_entries)
        assert len(audit_entries) == 2
        latest_entry = audit_entries[-1]
        assert latest_entry["status"] == EmailStatus.QUEUED.value
        assert "timestamp" in latest_entry
        assert latest_entry["message"] == "Status updated to queued"
        assert latest_entry["retry_count"] == 0
        
        # Update again
        updated = await email_service.update_status(
            db_session,
            email.id,
            EmailStatus.SENT
        )
        
        assert updated.status == EmailStatus.SENT
        assert updated.sent_at is not None


@pytest.mark.asyncio
async def test_update_status_handles_corrupted_audit_log(db_session):
    """Test status update handles corrupted audit log"""
    email_service = EmailService()

    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]

        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "caller_id": "service-a"
            }
        )

        email = await email_service.create_email(db_session, request)
        email.audit_log = "not-json"
        await db_session.commit()
        await db_session.refresh(email)

        updated = await email_service.update_status(
            db_session,
            email.id,
            EmailStatus.QUEUED
        )

        audit_entries = updated.audit_log
        if isinstance(audit_entries, str):
            audit_entries = json.loads(audit_entries)
        assert len(audit_entries) == 1
        assert audit_entries[0]["status"] == EmailStatus.QUEUED.value


@pytest.mark.asyncio
async def test_create_email_with_attachments(db_session):
    """Test email creation with attachments"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]
        
        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test with Attachments",
                "body": "Test body",
                "caller_id": "service-a",
                "attachments": [
                    {
                        "filename": "test.pdf",
                        "content_type": "application/pdf",
                        "content_base64": "dGVzdCBjb250ZW50"
                    },
                    {
                        "filename": "image.png",
                        "content_type": "image/png",
                        "content_base64": "aW1hZ2UgZGF0YQ=="
                    }
                ]
            }
        )
        
        email = await email_service.create_email(db_session, request)
        
        assert email.attachments is not None
        assert len(email.attachments) == 2
        assert email.attachments[0]["filename"] == "test.pdf"
        assert email.attachments[1]["filename"] == "image.png"

        with pytest.raises(ValidationError):
            EmailRequest(
                **{
                    "from": "sender@yourdomain.com",
                    "to": ["recipient@example.com"],
                    "subject": "Test with Attachments",
                    "body": "Test body",
                    "caller_id": "service-a",
                    "attachments": [
                        {
                            "filename": "bad.txt",
                            "content_type": "text/plain",
                            "content_base64": "not-base64",
                        }
                    ],
                }
            )

        too_many_attachments = [
            {"filename": f"file-{idx}.txt", "content_base64": "Zg=="}
            for idx in range(11)
        ]
        with pytest.raises(ValidationError):
            EmailRequest(
                **{
                    "from": "sender@yourdomain.com",
                    "to": ["recipient@example.com"],
                    "subject": "Test with Attachments",
                    "body": "Test body",
                    "caller_id": "service-a",
                    "attachments": too_many_attachments,
                }
            )


@pytest.mark.asyncio
async def test_create_email_with_custom_headers(db_session):
    """Test email creation with custom headers"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]
        mock_settings.get_allowed_headers_list.return_value = [
            "X-Custom-Header",
            "X-Priority"
        ]
        
        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "headers": {"X-Custom-Header": "value", "X-Priority": "high"},
                "caller_id": "service-a"
            }
        )
        
        email = await email_service.create_email(db_session, request)
        
        assert email.headers == {"X-Custom-Header": "value", "X-Priority": "high"}


@pytest.mark.asyncio
async def test_create_email_with_disallowed_headers(db_session):
    """Test email creation fails with disallowed headers"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]
        mock_settings.get_allowed_headers_list.return_value = [
            "X-Allowed-Header"
        ]
        
        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "headers": {"X-Disallowed": "value"},
                "caller_id": "service-a"
            }
        )
        
        with pytest.raises(ValueError, match="Headers not allowed"):
            await email_service.create_email(db_session, request)


@pytest.mark.asyncio
async def test_create_email_with_case_insensitive_header_validation(db_session):
    """Test header validation is case-insensitive"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]
        mock_settings.get_allowed_headers_list.return_value = [
            "X-Custom-Header"
        ]
        
        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "headers": {"x-CUSTOM-header": "value"},
                "caller_id": "service-a"
            }
        )
        
        email = await email_service.create_email(db_session, request)
        assert email.headers is not None


@pytest.mark.asyncio
async def test_create_email_with_multiple_disallowed_headers(db_session):
    """Test error message includes all disallowed headers"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]
        mock_settings.get_allowed_headers_list.return_value = [
            "X-Allowed"
        ]
        
        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "headers": {
                    "X-Disallowed-1": "value1",
                    "X-Disallowed-2": "value2",
                    "X-Allowed": "value3"
                },
                "caller_id": "service-a"
            }
        )
        
        match = r"X-Disallowed-1.*X-Disallowed-2|X-Disallowed-2.*X-Disallowed-1"
        with pytest.raises(ValueError, match=match) as exc_info:
            await email_service.create_email(db_session, request)
        
        error_msg = str(exc_info.value)
        assert "X-Disallowed-1" in error_msg
        assert "X-Disallowed-2" in error_msg


@pytest.mark.asyncio
async def test_create_email_with_tags(db_session):
    """Test email creation with tags"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]
        
        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "tags": ["marketing", "newsletter", "monthly"],
                "caller_id": "service-a"
            }
        )
        
        email = await email_service.create_email(db_session, request)
        
        assert email.tags == ["marketing", "newsletter", "monthly"]


@pytest.mark.asyncio
async def test_idempotency_payload_match_with_all_fields(db_session):
    """Test idempotency validates all fields including optional ones"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com", "bounce@yourdomain.com"
        ]
        mock_settings.get_allowed_headers_list.return_value = [
            "X-Custom"
        ]
        
        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "envelope_from": "bounce@yourdomain.com",
                "to": ["recipient@example.com"],
                "cc": ["cc@example.com"],
                "bcc": ["bcc@example.com"],
                "reply_to": "reply@yourdomain.com",
                "subject": "Test",
                "body": "Test body",
                "html": True,
                "headers": {"X-Custom": "value"},
                "tags": ["tag1"],
                "attachments": [{
                    "filename": "test.pdf",
                    "content_type": "application/pdf",
                    "content_base64": "dGVzdA=="
                }],
                "smtp_auth_profile_id": "profile-1",
                "idempotency_key": "unique-key-456",
                "caller_id": "service-a"
            }
        )
        
        # First submission
        email1 = await email_service.create_email(db_session, request)
        
        # Second submission with identical payload
        email2 = await email_service.create_email(db_session, request)
        
        # Should return the same email
        assert email1.id == email2.id


@pytest.mark.asyncio
async def test_idempotency_integrity_error_returns_existing_record():
    """Test idempotency handles concurrent insert race via IntegrityError"""
    from unittest.mock import AsyncMock
    from sqlalchemy.exc import IntegrityError
    from app.models.email import EmailRecord

    email_service = EmailService()
    email_service.validate_envelope_from = lambda _envelope_from: True
    email_service._payload_matches = lambda *_args, **_kwargs: True

    request = EmailRequest(
        **{
            "from": "sender@yourdomain.com",
            "to": ["recipient@example.com"],
            "subject": "Test",
            "body": "Test body",
            "idempotency_key": "key-race",
            "caller_id": "service-a",
        }
    )

    existing = EmailRecord(
        id="existing-id",
        caller_id="service-a",
        idempotency_key="key-race",
        from_address="sender@yourdomain.com",
        envelope_from="sender@yourdomain.com",
        to_addresses=["recipient@example.com"],
        subject="Test",
        body="Test body",
        status=EmailStatus.PENDING.value,
        retry_count=0,
        audit_log=[],
    )

    email_service.get_by_idempotency_key = AsyncMock(side_effect=[None, existing])

    class FakeDB:
        def __init__(self) -> None:
            self.rolled_back = False

        def add(self, _obj) -> None:
            return None

        async def commit(self) -> None:
            raise IntegrityError("stmt", {}, Exception("unique violation"))

        async def rollback(self) -> None:
            self.rolled_back = True

        async def refresh(self, _obj) -> None:
            pytest.fail("refresh should not be called on IntegrityError path")

    db = FakeDB()

    result = await email_service.create_email(db, request)
    assert result is existing
    assert db.rolled_back is True


@pytest.mark.asyncio
async def test_idempotency_payload_mismatch_cc_addresses(db_session):
    """Test idempotency detects CC address changes"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]
        
        request1 = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "cc": ["cc1@example.com"],
                "subject": "Test",
                "body": "Test body",
                "idempotency_key": "key-789",
                "caller_id": "service-a"
            }
        )
        
        await email_service.create_email(db_session, request1)
        
        request2 = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "cc": ["cc2@example.com"],
                "subject": "Test",
                "body": "Test body",
                "idempotency_key": "key-789",
                "caller_id": "service-a"
            }
        )
        
        with pytest.raises(IdempotencyPayloadMismatchError):
            await email_service.create_email(db_session, request2)


@pytest.mark.asyncio
async def test_idempotency_payload_mismatch_html_flag(db_session):
    """Test idempotency detects html flag changes"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]
        
        request1 = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "html": False,
                "idempotency_key": "key-html",
                "caller_id": "service-a"
            }
        )
        
        await email_service.create_email(db_session, request1)
        
        request2 = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "html": True,
                "idempotency_key": "key-html",
                "caller_id": "service-a"
            }
        )
        
        with pytest.raises(IdempotencyPayloadMismatchError):
            await email_service.create_email(db_session, request2)


@pytest.mark.asyncio
async def test_update_status_increment_retry(db_session):
    """Test status update increments retry count"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]
        
        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "caller_id": "service-a"
            }
        )
        
        email = await email_service.create_email(db_session, request)
        assert email.retry_count == 0
        
        # Update with retry increment
        updated = await email_service.update_status(
            db_session,
            email.id,
            EmailStatus.FAILED,
            error_message="SMTP error",
            increment_retry=True
        )
        
        assert updated.retry_count == 1
        assert updated.error_message == "SMTP error"

        updated = await email_service.update_status(
            db_session,
            email.id,
            EmailStatus.FAILED,
            error_message="",
        )
        assert updated.error_message == ""
        assert updated.retry_count == 1

        audit_entries = updated.audit_log
        if isinstance(audit_entries, str):
            audit_entries = json.loads(audit_entries)
        assert audit_entries[-1]["message"] == ""


@pytest.mark.asyncio
async def test_update_status_sets_sent_at(db_session):
    """Test status update sets sent_at for SENT status"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]
        
        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "caller_id": "service-a"
            }
        )
        
        email = await email_service.create_email(db_session, request)
        assert email.sent_at is None
        
        updated = await email_service.update_status(
            db_session,
            email.id,
            EmailStatus.SENT
        )
        
        assert updated.sent_at is not None


@pytest.mark.asyncio
async def test_update_status_nonexistent_email(db_session):
    """Test status update for nonexistent email raises error"""
    email_service = EmailService()
    
    with pytest.raises(ValueError, match="not found"):
        await email_service.update_status(
            db_session,
            "nonexistent-id",
            EmailStatus.SENT
        )


@pytest.mark.asyncio
async def test_get_by_id_returns_none_for_nonexistent(db_session):
    """Test get_by_id returns None for nonexistent email"""
    email_service = EmailService()
    
    result = await email_service.get_by_id(db_session, "nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_get_by_idempotency_key_returns_none_for_nonexistent(db_session):
    """Test get_by_idempotency_key returns None for nonexistent key"""
    email_service = EmailService()
    
    result = await email_service.get_by_idempotency_key(
        db_session, "caller", "nonexistent-key"
    )
    assert result is None


def test_parse_stored_addresses_with_list():
    """Test _parse_stored_addresses handles list input"""
    email_service = EmailService()
    
    result = email_service._parse_stored_addresses(
        ["addr1@example.com", "addr2@example.com"],
        "email-id",
        "to_addresses"
    )
    assert result == ["addr1@example.com", "addr2@example.com"]


def test_parse_stored_addresses_with_json_string():
    """Test _parse_stored_addresses handles JSON string input"""
    email_service = EmailService()
    
    result = email_service._parse_stored_addresses(
        '["addr1@example.com", "addr2@example.com"]',
        "email-id",
        "to_addresses"
    )
    assert result == ["addr1@example.com", "addr2@example.com"]


def test_parse_stored_addresses_with_none():
    """Test _parse_stored_addresses handles None input"""
    email_service = EmailService()
    
    result = email_service._parse_stored_addresses(
        None,
        "email-id",
        "to_addresses"
    )
    assert result == []


def test_parse_stored_addresses_with_invalid_json():
    """Test _parse_stored_addresses handles invalid JSON"""
    email_service = EmailService()
    
    with pytest.raises(StoredPayloadParseError):
        email_service._parse_stored_addresses(
            "not-valid-json",
            "email-id",
            "to_addresses"
        )


def test_normalize_attachments_with_none():
    """Test _normalize_attachments handles None"""
    email_service = EmailService()
    
    result = email_service._normalize_attachments(None)
    assert result is None


def test_normalize_attachments_with_list():
    """Test _normalize_attachments converts attachments to dicts"""
    email_service = EmailService()
    from app.schemas.email import EmailAttachment
    
    attachments = [
        EmailAttachment(
            filename="test.pdf",
            content_type="application/pdf",
            content_base64="dGVzdA=="
        )
    ]
    
    result = email_service._normalize_attachments(attachments)
    assert len(result) == 1
    assert result[0]["filename"] == "test.pdf"
    assert result[0]["content_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_create_email_creates_audit_log(db_session):
    """Test email creation initializes audit log"""
    email_service = EmailService()
    
    with patch('app.services.email.settings') as mock_settings:
        mock_settings.get_allowed_mailfrom_list.return_value = [
            "sender@yourdomain.com"
        ]
        
        request = EmailRequest(
            **{
                "from": "sender@yourdomain.com",
                "to": ["recipient@example.com"],
                "subject": "Test",
                "body": "Test body",
                "caller_id": "service-a"
            }
        )
        
        email = await email_service.create_email(db_session, request)
        
        assert email.audit_log is not None
        audit_entries = email.audit_log
        if isinstance(audit_entries, str):
            audit_entries = json.loads(audit_entries)
        assert len(audit_entries) == 1
        assert audit_entries[0]["status"] == EmailStatus.PENDING.value
        assert "timestamp" in audit_entries[0]
