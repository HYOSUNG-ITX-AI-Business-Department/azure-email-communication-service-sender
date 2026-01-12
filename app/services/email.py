from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.email import EmailRecord
from app.schemas.email import EmailRequest, EmailStatus, EmailAttachment
from app.config import settings
from datetime import datetime, timezone
import uuid
import json
import logging

logger = logging.getLogger(__name__)


class IdempotencyPayloadMismatchError(ValueError):
    """Raised when idempotency key is reused with different payload."""

    default_message = "Idempotency key reuse with different payload"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class InvalidMailFromError(ValueError):
    def __init__(self, envelope_from: str) -> None:
        super().__init__(
            f"envelope_from '{envelope_from}' is not in allowed MailFrom list"
        )
        self.envelope_from = envelope_from


class HeadersNotAllowedError(ValueError):
    def __init__(self, invalid_headers: list[str]) -> None:
        normalized = sorted(invalid_headers)
        super().__init__("Headers not allowed: " + ", ".join(normalized))
        self.invalid_headers = normalized


class EmailNotFoundError(ValueError):
    def __init__(self, email_id: str) -> None:
        super().__init__(f"Email {email_id} not found")
        self.email_id = email_id


class StoredPayloadParseError(Exception):
    def __init__(self, email_id: str, field_name: str) -> None:
        super().__init__(
            f"Invalid {field_name} JSON for email {email_id} while checking idempotency payload"
        )
        self.email_id = email_id
        self.field_name = field_name


class IdempotencyStoredPayloadCorruptionError(IdempotencyPayloadMismatchError):
    default_message = "Idempotency key reuse with corrupted stored payload"


class EmailService:
    """Service for email operations"""

    def _normalize_addresses(self, addresses: list[str] | None) -> list[str]:
        if addresses is None:
            return []
        return [address.strip().lower() for address in addresses]

    def _normalize_headers(
        self,
        headers: dict[str, str] | None,
    ) -> dict[str, str] | None:
        if headers is None:
            return None
        return {key.lower(): value for key, value in headers.items()}

    def _parse_stored_addresses(
        self,
        raw_addresses: list[str] | str | None,
        email_id: str,
        field_name: str,
    ) -> list[str]:
        if raw_addresses is None:
            # Normalize missing lists to empty so omission and [] are equivalent.
            return []
        if isinstance(raw_addresses, list):
            return self._normalize_addresses(raw_addresses)
        try:
            parsed = json.loads(raw_addresses)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.exception(
                "Invalid %s JSON for email %s while checking idempotency payload",
                field_name,
                email_id,
            )
            raise StoredPayloadParseError(email_id, field_name) from exc

        if not isinstance(parsed, list):
            logger.error(
                "Invalid %s JSON for email %s while checking idempotency payload: expected list",
                field_name,
                email_id,
            )
            raise StoredPayloadParseError(email_id, field_name)

        return self._normalize_addresses(parsed)

    def _payload_matches(
        self,
        existing: EmailRecord,
        email_request: EmailRequest,
        envelope_from: str,
    ) -> bool:
        stored_to = self._parse_stored_addresses(
            existing.to_addresses, existing.id, "to_addresses"
        )
        stored_cc = self._parse_stored_addresses(
            existing.cc_addresses, existing.id, "cc_addresses"
        )
        stored_bcc = self._parse_stored_addresses(
            existing.bcc_addresses, existing.id, "bcc_addresses"
        )

        stored_headers = self._parse_stored_json(
            existing.headers, existing.id, "headers"
        )
        if stored_headers is not None and not isinstance(stored_headers, dict):
            raise StoredPayloadParseError(existing.id, "headers")

        stored_tags = self._parse_stored_json(
            existing.tags, existing.id, "tags"
        )
        if stored_tags is not None and not isinstance(stored_tags, list):
            raise StoredPayloadParseError(existing.id, "tags")

        stored_attachments = self._parse_stored_json(
            existing.attachments, existing.id, "attachments"
        )
        if stored_attachments is not None and not isinstance(stored_attachments, list):
            raise StoredPayloadParseError(existing.id, "attachments")

        request_attachments = self._normalize_attachments(email_request.attachments)

        stored_reply_to = existing.reply_to.lower() if existing.reply_to else None
        request_reply_to = (
            email_request.reply_to.lower() if email_request.reply_to else None
        )

        stored_headers_normalized = self._normalize_headers(stored_headers)
        request_headers_normalized = self._normalize_headers(email_request.headers)

        return (
            existing.from_address.lower() == email_request.from_address.lower()
            and existing.envelope_from.lower() == envelope_from.lower()
            and existing.smtp_auth_profile_id == email_request.smtp_auth_profile_id
            and stored_reply_to == request_reply_to
            and stored_to == self._normalize_addresses(email_request.to)
            and stored_cc == self._normalize_addresses(email_request.cc)
            and stored_bcc == self._normalize_addresses(email_request.bcc)
            and stored_headers_normalized == request_headers_normalized
            and stored_tags == email_request.tags
            and stored_attachments == request_attachments
            and existing.subject == email_request.subject
            and existing.body == email_request.body
            and bool(existing.is_html) == email_request.html
        )
    
    def validate_envelope_from(self, envelope_from: str) -> bool:
        """Validate that envelope_from is in allowed list"""
        allowed = settings.get_allowed_mailfrom_list()
        allowed_normalized = {address.strip().lower() for address in allowed}
        return envelope_from.strip().lower() in allowed_normalized

    def _parse_stored_json(
        self,
        raw_value: dict | list | str | None,
        email_id: str,
        field_name: str,
    ) -> dict | list | None:
        if raw_value is None:
            # Preserve None to distinguish missing fields from invalid payloads.
            return None
        if isinstance(raw_value, (dict, list)):
            return raw_value
        try:
            parsed = json.loads(raw_value)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.exception(
                "Invalid %s JSON for email %s while checking idempotency payload",
                field_name,
                email_id,
            )
            raise StoredPayloadParseError(email_id, field_name) from exc

        if not isinstance(parsed, (dict, list)):
            logger.error(
                "Invalid %s JSON for email %s while checking idempotency payload: expected dict/list",
                field_name,
                email_id,
            )
            raise StoredPayloadParseError(email_id, field_name)

        return parsed

    def _parse_audit_log(
        self,
        raw_audit_log: list | dict | str | None,
        email_id: str,
    ) -> list[dict]:
        if raw_audit_log is None:
            return []
        if isinstance(raw_audit_log, list):
            # Copy to ensure SQLAlchemy detects JSON list updates.
            return list(raw_audit_log)
        if isinstance(raw_audit_log, str):
            try:
                # Decode at most twice to support legacy double-encoded JSON.
                parsed: object = raw_audit_log
                for _ in range(2):
                    if not isinstance(parsed, str):
                        break
                    parsed = json.loads(parsed)
            except (json.JSONDecodeError, TypeError):
                logger.exception(
                    "Corrupted audit_log for email %s, resetting to empty list",
                    email_id,
                )
                return []
            if isinstance(parsed, list):
                return list(parsed)
        return []

    def _normalize_attachments(
        self,
        attachments: list[EmailAttachment] | None,
    ) -> list[dict] | None:
        if attachments is None:
            return None
        return [attachment.model_dump() for attachment in attachments]
    
    async def create_email(
        self,
        db: AsyncSession,
        email_request: EmailRequest
    ) -> EmailRecord:
        """
        Create and validate email record
        
        Args:
            db: Database session
            email_request: Email request data
            
        Returns:
            EmailRecord: Created email record
            
        Raises:
            ValueError: If validation fails
        """
        # Default policy: from == envelope_from (aligned)
        envelope_from = email_request.envelope_from or email_request.from_address
        
        # Validate envelope_from is in allowed list
        if not self.validate_envelope_from(envelope_from):
            raise InvalidMailFromError(envelope_from)
        
        # Check idempotency per caller when a key is provided
        if email_request.idempotency_key:
            existing = await self.get_by_idempotency_key(
                db, email_request.caller_id, email_request.idempotency_key
            )
            if existing:
                try:
                    payload_matches = self._payload_matches(
                        existing,
                        email_request,
                        envelope_from,
                    )
                except StoredPayloadParseError as exc:
                    raise IdempotencyStoredPayloadCorruptionError() from exc

                if not payload_matches:
                    raise IdempotencyPayloadMismatchError()
                logger.info(
                    "Duplicate request with idempotency key: %s for caller: %s",
                    email_request.idempotency_key,
                    email_request.caller_id,
                )
                return existing
        
        # Create audit log
        audit_log = [{
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": EmailStatus.PENDING.value,
            "message": "Email created"
        }]
        
        headers = email_request.headers
        if headers:
            allowed_headers = {
                header.lower()
                for header in settings.get_allowed_headers_list()
            }
            invalid_headers = [
                header
                for header in headers
                if header.lower() not in allowed_headers
            ]
            if invalid_headers:
                raise HeadersNotAllowedError(invalid_headers)

        attachments_payload = self._normalize_attachments(email_request.attachments)

        # Create email record
        email_record = EmailRecord(
            id=str(uuid.uuid4()),
            caller_id=email_request.caller_id,
            idempotency_key=email_request.idempotency_key,
            from_address=email_request.from_address,
            envelope_from=envelope_from,
            smtp_auth_profile_id=email_request.smtp_auth_profile_id,
            reply_to=email_request.reply_to,
            to_addresses=email_request.to,
            cc_addresses=email_request.cc,
            bcc_addresses=email_request.bcc,
            headers=headers,
            tags=email_request.tags,
            subject=email_request.subject,
            body=email_request.body,
            is_html=1 if email_request.html else 0,
            attachments=attachments_payload,
            status=EmailStatus.PENDING.value,
            retry_count=0,
            audit_log=audit_log
        )
        
        db.add(email_record)
        try:
            await db.commit()
        except IntegrityError as integrity_err:
            await db.rollback()
            if email_request.idempotency_key:
                existing = await self.get_by_idempotency_key(
                    db, email_request.caller_id, email_request.idempotency_key
                )
                if existing:
                    try:
                        payload_matches = self._payload_matches(
                            existing,
                            email_request,
                            envelope_from,
                        )
                    except StoredPayloadParseError as parse_exc:
                        raise IdempotencyStoredPayloadCorruptionError() from parse_exc

                    if not payload_matches:
                        raise IdempotencyPayloadMismatchError() from integrity_err
                    return existing
            raise

        await db.refresh(email_record)
        
        logger.info(
            "Created email record %s for caller %s",
            email_record.id,
            email_request.caller_id,
        )
        return email_record
    
    async def get_by_id(self, db: AsyncSession, email_id: str) -> EmailRecord | None:
        """Get email by ID"""
        result = await db.execute(
            select(EmailRecord).where(EmailRecord.id == email_id)
        )
        return result.scalar_one_or_none()
    
    async def get_by_idempotency_key(
        self, db: AsyncSession, caller_id: str, idempotency_key: str
    ) -> EmailRecord | None:
        """Get email by caller_id and idempotency key"""
        result = await db.execute(
            select(EmailRecord).where(
                EmailRecord.caller_id == caller_id,
                EmailRecord.idempotency_key == idempotency_key
            )
        )
        return result.scalar_one_or_none()
    
    async def transition_to_sending(self, db: AsyncSession, email_id: str) -> bool:
        """Atomically transition an email to SENDING.

        Returns True if we performed the transition; False if another worker already
        transitioned it or it is not eligible for sending.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            update(EmailRecord)
            .where(
                EmailRecord.id == email_id,
                EmailRecord.status.in_(
                    [
                        EmailStatus.PENDING.value,
                        EmailStatus.QUEUED.value,
                        EmailStatus.FAILED.value,
                    ]
                ),
            )
            .values(
                status=EmailStatus.SENDING.value,
                updated_at=now,
            )
        )
        result = await db.execute(stmt)
        await db.commit()
        return bool(getattr(result, "rowcount", 0))

    async def update_status(
        self,
        db: AsyncSession,
        email_id: str,
        status: EmailStatus,
        error_message: str | None = None,
        *,
        increment_retry: bool = False,
    ) -> EmailRecord:
        """Update email status with audit trail"""
        email = await self.get_by_id(db, email_id)
        if not email:
            raise EmailNotFoundError(email_id)

        # Update status
        email.status = status.value
        email.updated_at = datetime.now(timezone.utc)

        if error_message is not None:
            email.error_message = error_message

        if increment_retry:
            email.retry_count += 1

        if status == EmailStatus.SENT:
            email.sent_at = datetime.now(timezone.utc)

        # Update audit log
        audit_log = self._parse_audit_log(email.audit_log, email_id)
        audit_log.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": status.value,
                "message": (
                    error_message
                    if error_message is not None
                    else f"Status updated to {status.value}"
                ),
                "retry_count": email.retry_count,
            }
        )
        email.audit_log = audit_log

        await db.commit()
        await db.refresh(email)

        logger.info("Updated email %s status to %s", email_id, status.value)
        return email


# Global email service instance
email_service = EmailService()