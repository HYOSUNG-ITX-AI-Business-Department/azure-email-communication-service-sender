from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class EmailStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    DLQ = "dlq"


class EmailRequest(BaseModel):
    """Request model for sending an email"""
    from_address: EmailStr = Field(..., alias="from", description="Header From (RFC 5322.From)")
    envelope_from: Optional[EmailStr] = Field(None, description="Envelope Sender (RFC 5321.MailFrom)")
    to: List[EmailStr] = Field(..., description="Recipient addresses")
    cc: Optional[List[EmailStr]] = Field(None, description="CC addresses")
    bcc: Optional[List[EmailStr]] = Field(None, description="BCC addresses")
    subject: str = Field(..., description="Email subject")
    body: str = Field(..., description="Email body (plain text or HTML)")
    html: Optional[bool] = Field(False, description="Whether body is HTML")
    idempotency_key: Optional[str] = Field(None, description="Idempotency key for duplicate prevention")
    
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
    to: List[str]
    subject: str
    created_at: datetime
    updated_at: datetime
    retry_count: int
    error_message: Optional[str] = None
    sent_at: Optional[datetime] = None
