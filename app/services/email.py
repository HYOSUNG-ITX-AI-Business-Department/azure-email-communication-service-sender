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


class EmailService:
    """Service for email operations"""
    
    def validate_envelope_from(self, envelope_from: str) -> bool:
        """Validate that envelope_from is in allowed list"""
        allowed = settings.get_allowed_mailfrom_list()
        return envelope_from in allowed
    
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
        
        # Check idempotency
        if email_request.idempotency_key:
            existing = await self.get_by_idempotency_key(
                db, email_request.idempotency_key
            )
            if existing:
                logger.info(f"Duplicate request with idempotency key: {email_request.idempotency_key}")
                return existing
        
        # Create audit log
        audit_log = [{
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": EmailStatus.PENDING,
            "message": "Email created"
        }]
        
        # Create email record
        email_record = EmailRecord(
            id=str(uuid.uuid4()),
            idempotency_key=email_request.idempotency_key,
            from_address=email_request.from_address,
            envelope_from=envelope_from,
            to_addresses=json.dumps(email_request.to),
            cc_addresses=json.dumps(email_request.cc) if email_request.cc else None,
            bcc_addresses=json.dumps(email_request.bcc) if email_request.bcc else None,
            subject=email_request.subject,
            body=email_request.body,
            is_html=1 if email_request.html else 0,
            status=EmailStatus.PENDING,
            retry_count=0,
            audit_log=json.dumps(audit_log)
        )
        
        db.add(email_record)
        await db.commit()
        await db.refresh(email_record)
        
        logger.info(f"Created email record {email_record.id}")
        return email_record
    
    async def get_by_id(self, db: AsyncSession, email_id: str) -> EmailRecord:
        """Get email by ID"""
        result = await db.execute(
            select(EmailRecord).where(EmailRecord.id == email_id)
        )
        return result.scalar_one_or_none()
    
    async def get_by_idempotency_key(
        self, db: AsyncSession, idempotency_key: str
    ) -> EmailRecord:
        """Get email by idempotency key"""
        result = await db.execute(
            select(EmailRecord).where(EmailRecord.idempotency_key == idempotency_key)
        )
        return result.scalar_one_or_none()
    
    async def update_status(
        self,
        db: AsyncSession,
        email_id: str,
        status: EmailStatus,
        error_message: str = None,
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
        audit_log = json.loads(email.audit_log) if email.audit_log else []
        audit_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "message": error_message or f"Status updated to {status}",
            "retry_count": email.retry_count
        })
        email.audit_log = json.dumps(audit_log)
        
        await db.commit()
        await db.refresh(email)
        
        logger.info(f"Updated email {email_id} status to {status}")
        return email


# Global email service instance
email_service = EmailService()
