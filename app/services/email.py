from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.email import EmailRecord
from app.schemas.email import EmailRequest, EmailStatus
from app.config import settings
from datetime import datetime, timezone
import uuid
import json
import logging

logger = logging.getLogger(__name__)


class IdempotencyPayloadMismatchError(ValueError):
    """Raised when idempotency key is reused with different payload."""


class EmailService:
    """Service for email operations"""

    def _normalize_addresses(self, addresses: list[str] | None) -> list[str]:
        return addresses or []

    def _parse_stored_addresses(
        self,
        raw_addresses: list[str] | str | None,
        email_id: str,
        field_name: str,
    ) -> list[str] | None:
        if raw_addresses is None:
            return []
        if isinstance(raw_addresses, list):
            return raw_addresses
        try:
            return json.loads(raw_addresses)
        except (json.JSONDecodeError, TypeError):
            logger.error(
                "Invalid %s JSON for email %s while checking idempotency payload",
                field_name,
                email_id,
            )
            return None

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
        if stored_to is None or stored_cc is None or stored_bcc is None:
            return False

        return (
            existing.from_address == email_request.from_address
            and existing.envelope_from == envelope_from
            and stored_to == self._normalize_addresses(email_request.to)
            and stored_cc == self._normalize_addresses(email_request.cc)
            and stored_bcc == self._normalize_addresses(email_request.bcc)
            and existing.subject == email_request.subject
            and existing.body == email_request.body
            and bool(existing.is_html) == bool(email_request.html)
        )
    
    def validate_envelope_from(self, envelope_from: str) -> bool:
        """Validate that envelope_from is in allowed list"""
        allowed = settings.get_allowed_mailfrom_list()
        allowed_normalized = {address.lower() for address in allowed}
        return envelope_from.lower() in allowed_normalized
    
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
            raise ValueError(
                f"envelope_from '{envelope_from}' is not in allowed MailFrom list"
            )
        
        # Check idempotency per caller
        # Note: idempotency is only enforced when both caller_id and idempotency_key are provided
        if email_request.idempotency_key and email_request.caller_id:
            existing = await self.get_by_idempotency_key(
                db, email_request.caller_id, email_request.idempotency_key
            )
            if existing:
                if not self._payload_matches(existing, email_request, envelope_from):
                    raise IdempotencyPayloadMismatchError(
                        "Idempotency key reuse with different payload"
                    )
                logger.info(f"Duplicate request with idempotency key: {email_request.idempotency_key} for caller: {email_request.caller_id}")
                return existing
        
        # Create audit log
        audit_log = [{
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": EmailStatus.PENDING.value,
            "message": "Email created"
        }]
        
        # Create email record
        email_record = EmailRecord(
            id=str(uuid.uuid4()),
            caller_id=email_request.caller_id,
            idempotency_key=email_request.idempotency_key,
            from_address=email_request.from_address,
            envelope_from=envelope_from,
            to_addresses=email_request.to,
            cc_addresses=email_request.cc,
            bcc_addresses=email_request.bcc,
            subject=email_request.subject,
            body=email_request.body,
            is_html=1 if email_request.html else 0,
            status=EmailStatus.PENDING,
            retry_count=0,
            audit_log=audit_log
        )
        
        db.add(email_record)
        await db.commit()
        await db.refresh(email_record)
        
        logger.info(f"Created email record {email_record.id} for caller {email_request.caller_id}")
        return email_record
    
    async def get_by_id(self, db: AsyncSession, email_id: str) -> EmailRecord:
        """Get email by ID"""
        result = await db.execute(
            select(EmailRecord).where(EmailRecord.id == email_id)
        )
        return result.scalar_one_or_none()
    
    async def get_by_idempotency_key(
        self, db: AsyncSession, caller_id: str, idempotency_key: str
    ) -> EmailRecord:
        """Get email by caller_id and idempotency key"""
        result = await db.execute(
            select(EmailRecord).where(
                EmailRecord.caller_id == caller_id,
                EmailRecord.idempotency_key == idempotency_key
            )
        )
        return result.scalar_one_or_none()
    
    async def update_status(
        self,
        db: AsyncSession,
        email_id: str,
        status: EmailStatus,
        error_message: str | None = None,
        *,
        increment_retry: bool = False
    ) -> EmailRecord:
        """Update email status with audit trail"""
        email = await self.get_by_id(db, email_id)
        if not email:
            raise ValueError(f"Email {email_id} not found")
        
        # Update status
        email.status = status
        email.updated_at = datetime.now(timezone.utc)
        
        if error_message:
            email.error_message = error_message
        
        if increment_retry:
            email.retry_count += 1
        
        if status == EmailStatus.SENT:
            email.sent_at = datetime.now(timezone.utc)
        
        # Update audit log
        audit_log = []
        if email.audit_log:
            if isinstance(email.audit_log, list):
                audit_log = email.audit_log
            else:
                try:
                    audit_log = json.loads(email.audit_log)
                    if isinstance(audit_log, str):
                        audit_log = json.loads(audit_log)
                    if not isinstance(audit_log, list):
                        audit_log = []
                except (json.JSONDecodeError, TypeError):
                    logger.exception(
                        "Corrupted audit_log for email %s, resetting to empty list",
                        email_id,
                    )
        audit_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status.value,
            "message": error_message or f"Status updated to {status.value}",
            "retry_count": email.retry_count
        })
        email.audit_log = audit_log
        
        await db.commit()
        await db.refresh(email)
        
        logger.info(f"Updated email {email_id} status to {status}")
        return email


# Global email service instance
email_service = EmailService()
