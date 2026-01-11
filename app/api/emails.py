from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas.email import (
    EmailRequest,
    EmailResponse,
    EmailStatusResponse,
    EmailStatus,
    QueueStatsResponse,
)
from app.services.email import (
    IdempotencyPayloadMismatchError,
    IdempotencyStoredPayloadCorruptionError,
    email_service,
)
from app.services.queue import queue_service
import json
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/emails", tags=["emails"])


async def get_authenticated_caller_id(
    x_caller_id: str = Header(..., alias="X-Caller-Id"),
) -> str:
    """Return the caller id asserted by a trusted upstream.

    This project currently treats `X-Caller-Id` as an authenticated identity
    provided by a trusted upstream (e.g., API gateway/service mesh). For
    internet-facing deployments, replace this with verified authentication
    (JWT/API key) and derive the caller id from that identity.
    """
    return x_caller_id


@router.post("/", response_model=EmailResponse, status_code=status.HTTP_202_ACCEPTED)
async def send_email(
    email_request: EmailRequest,
    db: AsyncSession = Depends(get_db),
    authenticated_caller_id: str = Depends(get_authenticated_caller_id),
):
    """
    Send an email
    
    The service validates and stores the email, then queues it for sending.
    
    - **from**: Header From address (RFC 5322.From)
    - **envelope_from**: Envelope Sender (RFC 5321.MailFrom) - defaults to 'from' if not provided
    - **to**: List of recipient email addresses
    - **cc**: Optional list of CC addresses
    - **bcc**: Optional list of BCC addresses
    - **subject**: Email subject
    - **body**: Email body content
    - **html**: Whether body is HTML (default: false)
    - **reply_to**: Optional Reply-To address
    - **attachments**: Optional attachments list
    - **headers**: Optional custom headers (allowlist enforced)
    - **tags**: Optional tags for tracking
    - **caller_id**: Caller identifier for multi-tenant isolation
    - **idempotency_key**: Optional key to prevent duplicate submissions
    - **X-Caller-Id**: Caller identifier header (must be set by a trusted upstream and match caller_id)
    """
    try:
        if email_request.caller_id != authenticated_caller_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="caller_id does not match authenticated caller",
            )

        # Create and validate email record
        email_record = await email_service.create_email(db, email_request)
        
        # Update status to queued
        email_record = await email_service.update_status(
            db, email_record.id, EmailStatus.QUEUED
        )
        
        # Add to queue
        await queue_service.enqueue(email_record.id)
        
        logger.info("Email %s submitted successfully", email_record.id)
        
        return EmailResponse(
            email_id=email_record.id,
            status=EmailStatus.QUEUED,
            message="Email queued for sending",
            created_at=email_record.created_at
        )
        
    except HTTPException:
        raise
    except IdempotencyStoredPayloadCorruptionError as err:
        logger.exception("Idempotency stored payload corruption")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(err),
        ) from err
    except IdempotencyPayloadMismatchError as err:
        logger.exception("Idempotency payload mismatch")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(err),
        ) from err
    except ValueError as err:
        logger.exception("Validation error")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except Exception as err:
        logger.exception("Error submitting email")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit email",
        ) from err


@router.get("/{email_id}", response_model=EmailStatusResponse)
async def get_email_status(
    email_id: str,
    db: AsyncSession = Depends(get_db),
    authenticated_caller_id: str = Depends(get_authenticated_caller_id),
):
    """
    Get email status by ID
    
    Returns the current status, retry count, and other details about the email.
    """
    try:
        email = await email_service.get_by_id(db, email_id)
        
        if not email or email.caller_id != authenticated_caller_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Email {email_id} not found"
            )
        
        # Parse to_addresses with error handling
        try:
            parsed_to = email.to_addresses
            if isinstance(parsed_to, str):
                parsed_to = json.loads(parsed_to)
            if parsed_to is None:
                parsed_to = []
        except (json.JSONDecodeError, TypeError):
            logger.exception(
                "Error parsing to_addresses for email %s",
                email_id,
            )
            # Fall back to a safe default - use empty list
            parsed_to = []
        
        return EmailStatusResponse(
            email_id=email.id,
            status=EmailStatus(email.status),
            from_address=email.from_address,
            envelope_from=email.envelope_from,
            to=parsed_to,
            subject=email.subject,
            created_at=email.created_at,
            updated_at=email.updated_at,
            retry_count=email.retry_count,
            error_message=email.error_message,
            sent_at=email.sent_at,
            caller_id=email.caller_id,
            smtp_auth_profile_id=email.smtp_auth_profile_id,
        )
        
    except HTTPException:
        raise
    except Exception as err:
        logger.exception("Error getting email status")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get email status"
        ) from err


@router.get("/", response_model=QueueStatsResponse)
async def get_queue_stats(
    authenticated_caller_id: str = Depends(get_authenticated_caller_id),
):
    """
    Get queue statistics
    
    Returns current queue sizes for monitoring.
    """
    try:
        return {
            "queue_size": await queue_service.get_queue_size(),
            "processing_size": await queue_service.get_processing_size(),
            "dlq_size": await queue_service.get_dlq_size()
        }
    except Exception as err:
        logger.exception("Error getting queue stats")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get queue statistics"
        ) from err
