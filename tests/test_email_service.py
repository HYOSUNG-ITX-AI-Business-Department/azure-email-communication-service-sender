import json
import pytest
from unittest.mock import patch
from app.services.email import EmailService, IdempotencyPayloadMismatchError
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
                "body": "Test body"
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
                "body": "Test body"
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
                "body": "Test body"
            }
        )

        email = await email_service.create_email(db_session, request)

        assert email.envelope_from.lower() == "sender@yourdomain.com"


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
                "body": "Test body"
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
                "body": "Test body"
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
        assert len(audit_entries) >= 1
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
                "body": "Test body"
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
