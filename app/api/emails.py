from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas.email import EmailRequest, EmailResponse, EmailStatusResponse, EmailStatus
from app.services.email import email_service
from app.services.queue import queue_service
import json
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/emails", tags=["emails"])


@router.post("/", response_model=EmailResponse, status_code=status.HTTP_201_CREATED)
async def send_email(
    email_request: EmailRequest,
    db: AsyncSession = Depends(get_db)
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
    - **idempotency_key**: Optional key to prevent duplicate submissions
    """
    try:
        # Create and validate email record
        email_record = await email_service.create_email(db, email_request)
        
        # Update status to queued
        email_record = await email_service.update_status(
            db, email_record.id, EmailStatus.QUEUED
        )
        
        # Add to queue
        await queue_service.enqueue(email_record.id)
        
        logger.info(f"Email {email_record.id} submitted successfully")
        
        return EmailResponse(
            email_id=email_record.id,
            status=EmailStatus.QUEUED,
            message="Email queued for sending",
            created_at=email_record.created_at
        )
        
    except ValueError as e:
        logger.error(f"Validation error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error submitting email: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit email"
        )


@router.get("/{email_id}", response_model=EmailStatusResponse)
async def get_email_status(
    email_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get email status by ID
    
    Returns the current status, retry count, and other details about the email.
    """
    try:
        email = await email_service.get_by_id(db, email_id)
        
        if not email:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Email {email_id} not found"
            )
        
        # Parse to_addresses with error handling
        try:
            parsed_to = json.loads(email.to_addresses)
        except (json.JSONDecodeError, TypeError) as parse_error:
            logger.error(f"Error parsing to_addresses for email {email_id}: {str(parse_error)}")
            # Fall back to a safe default - wrap raw value in a list if it looks like an email
            parsed_to = [email.to_addresses] if email.to_addresses else []
        
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
            caller_id=email.caller_id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting email status: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get email status"
        )


@router.get("/", response_model=dict)
async def get_queue_stats():
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
    except Exception as e:
        logger.error(f"Error getting queue stats: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get queue statistics"
        )
