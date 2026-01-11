from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional
from datetime import datetime
from enum import Enum


class EmailStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    DLQ = "dlq"


class EmailAttachment(BaseModel):
    """Attachment model for email requests"""
    filename: str = Field(..., min_length=1, description="Attachment filename")
    content_type: str = Field(
        "application/octet-stream",
        description="Attachment MIME type",
    )
    content_base64: str = Field(
        ...,
        min_length=1,
        description="Base64-encoded attachment content",
    )


class EmailRequest(BaseModel):
    """Request model for sending an email"""
    from_address: EmailStr = Field(..., alias="from", description="Header From (RFC 5322.From)")
    envelope_from: Optional[EmailStr] = Field(None, description="Envelope Sender (RFC 5321.MailFrom)")
    to: list[EmailStr] = Field(..., min_length=1, description="Recipient addresses")
    cc: Optional[list[EmailStr]] = Field(None, description="CC addresses")
    bcc: Optional[list[EmailStr]] = Field(None, description="BCC addresses")
    subject: str = Field(..., description="Email subject")
    body: str = Field(..., description="Email body (plain text or HTML)")
    html: bool = Field(default=False, description="Whether body is HTML")
    reply_to: Optional[EmailStr] = Field(None, description="Reply-To address")
    attachments: Optional[list[EmailAttachment]] = Field(
        None,
        description="Email attachments",
    )
    headers: Optional[dict[str, str]] = Field(
        None,
        description="Custom headers (allowlist enforced)",
    )
    tags: Optional[list[str]] = Field(None, description="Tags for tracking")
    idempotency_key: Optional[str] = Field(None, description="Idempotency key for duplicate prevention")
    caller_id: str = Field(..., description="Caller identifier for multi-tenant isolation")
    smtp_auth_profile_id: Optional[str] = Field(
        None,
        description="SMTP auth profile identifier for audit correlation",
    )
    
    model_config = ConfigDict(populate_by_name=True)


class EmailResponse(BaseModel):
    """Response model for email submission"""
    email_id: str
    status: EmailStatus
    message: str
    created_at: datetime


class EmailStatusResponse(BaseModel):
    """Response model for email status check"""
    email_id: str
    status: EmailStatus
    from_address: str
    envelope_from: str
    to: list[str]
    subject: str
    created_at: datetime
    updated_at: datetime
    retry_count: int
    error_message: Optional[str] = None
    sent_at: Optional[datetime] = None
    caller_id: str
    smtp_auth_profile_id: Optional[str] = None


class QueueStatsResponse(BaseModel):
    """Response model for queue stats"""
    queue_size: int
    processing_size: int
    dlq_size: int
