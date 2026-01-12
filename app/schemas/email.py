import base64
import binascii

from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator
from typing import Optional
from datetime import datetime
from enum import Enum

MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MiB
MAX_ATTACHMENT_BASE64_CHARS = 14_000_000
MAX_ATTACHMENT_FILENAME_CHARS = 255
MAX_ATTACHMENT_CONTENT_TYPE_CHARS = 255
MAX_CALLER_ID_CHARS = 256
MAX_IDEMPOTENCY_KEY_CHARS = 256
MAX_SMTP_AUTH_PROFILE_ID_CHARS = 256
MAX_SUBJECT_CHARS = 1000
MAX_BODY_CHARS = 1_000_000
MAX_HEADERS = 50
MAX_HEADER_NAME_CHARS = 128
MAX_HEADER_VALUE_CHARS = 2048
MAX_TAGS = 50
MAX_TAG_CHARS = 128


class EmailStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    DLQ = "dlq"


class EmailAttachment(BaseModel):
    """Attachment model for email requests"""
    filename: str = Field(
        ...,
        min_length=1,
        max_length=MAX_ATTACHMENT_FILENAME_CHARS,
        description="Attachment filename",
    )
    content_type: str = Field(
        "application/octet-stream",
        max_length=MAX_ATTACHMENT_CONTENT_TYPE_CHARS,
        description="Attachment MIME type",
    )
    content_base64: str = Field(
        ...,
        min_length=1,
        max_length=MAX_ATTACHMENT_BASE64_CHARS,
        description="Base64-encoded attachment content",
    )

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError(  # noqa: TRY003
                "filename must not contain CR/LF characters"
            )
        if value in {".", ".."}:
            raise ValueError("filename must not be a path traversal token")  # noqa: TRY003
        if "/" in value or "\\" in value:
            raise ValueError("filename must not contain path separators")  # noqa: TRY003
        return value

    @field_validator("content_type")
    @classmethod
    def _validate_content_type(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError(  # noqa: TRY003
                "content_type must not contain CR/LF characters"
            )
        return value

    @field_validator("content_base64")
    @classmethod
    def _validate_content_base64(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("content_base64 must be valid base64") from exc  # noqa: TRY003

        if len(decoded) > MAX_ATTACHMENT_BYTES:
            raise ValueError("content_base64 exceeds maximum allowed size")  # noqa: TRY003

        return value


class EmailRequest(BaseModel):
    """Request model for sending an email"""
    from_address: EmailStr = Field(..., alias="from", description="Header From (RFC 5322.From)")
    envelope_from: Optional[EmailStr] = Field(None, description="Envelope Sender (RFC 5321.MailFrom)")
    to: list[EmailStr] = Field(..., min_length=1, description="Recipient addresses")
    cc: Optional[list[EmailStr]] = Field(None, description="CC addresses")
    bcc: Optional[list[EmailStr]] = Field(None, description="BCC addresses")
    subject: str = Field(
        ...,
        max_length=MAX_SUBJECT_CHARS,
        description="Email subject",
    )
    body: str = Field(
        ...,
        max_length=MAX_BODY_CHARS,
        description="Email body (plain text or HTML)",
    )
    html: bool = Field(default=False, description="Whether body is HTML")
    reply_to: Optional[EmailStr] = Field(None, description="Reply-To address")
    attachments: Optional[list[EmailAttachment]] = Field(
        None,
        max_length=MAX_ATTACHMENTS,
        description="Email attachments",
    )
    headers: Optional[dict[str, str]] = Field(
        None,
        description="Custom headers (allowlist enforced)",
    )
    tags: Optional[list[str]] = Field(
        None,
        max_length=MAX_TAGS,
        description="Tags for tracking",
    )
    idempotency_key: Optional[str] = Field(
        None,
        max_length=MAX_IDEMPOTENCY_KEY_CHARS,
        description="Idempotency key for duplicate prevention",
    )
    caller_id: str = Field(
        ...,
        max_length=MAX_CALLER_ID_CHARS,
        description="Caller identifier for multi-tenant isolation",
    )
    smtp_auth_profile_id: Optional[str] = Field(
        None,
        max_length=MAX_SMTP_AUTH_PROFILE_ID_CHARS,
        description="SMTP auth profile identifier for audit correlation",
    )
    
    model_config = ConfigDict(populate_by_name=True)

    @field_validator(
        "subject",
        "from_address",
        "envelope_from",
        "reply_to",
        "idempotency_key",
        "caller_id",
        "smtp_auth_profile_id",
    )
    @classmethod
    def _reject_crlf_in_string_fields(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if "\r" in value or "\n" in value:
            raise ValueError("CR/LF characters are not allowed")  # noqa: TRY003
        return value

    @field_validator("to", "cc", "bcc")
    @classmethod
    def _reject_crlf_in_address_lists(
        cls, value: list[str] | None
    ) -> list[str] | None:
        if value is None:
            return value
        for address in value:
            if "\r" in address or "\n" in address:
                raise ValueError("CR/LF characters are not allowed")  # noqa: TRY003
        return value

    @field_validator("headers")
    @classmethod
    def _reject_crlf_in_headers(
        cls, value: dict[str, str] | None
    ) -> dict[str, str] | None:
        if value is None:
            return value
        if len(value) > MAX_HEADERS:
            raise ValueError(  # noqa: TRY003
                f"Too many headers: {len(value)} > {MAX_HEADERS}"
            )
        for header_name, header_value in value.items():
            if "\r" in header_name or "\n" in header_name:
                header_name_escaped = header_name.replace("\r", "\\r").replace(
                    "\n", "\\n"
                )
                raise ValueError(  # noqa: TRY003
                    f"Header name contains CR/LF: {header_name_escaped!r}"
                )
            if "\r" in header_value or "\n" in header_value:
                raise ValueError(  # noqa: TRY003
                    f"Header '{header_name}' value contains CR/LF"
                )
            if len(header_name) > MAX_HEADER_NAME_CHARS:
                header_name_prefix = header_name[:20]
                header_name_suffix = "..." if len(header_name) > 20 else ""
                raise ValueError(  # noqa: TRY003
                    f"Header name '{header_name_prefix}{header_name_suffix}' is too long "
                    f"({len(header_name)} > {MAX_HEADER_NAME_CHARS})"
                )
            if len(header_value) > MAX_HEADER_VALUE_CHARS:
                raise ValueError(  # noqa: TRY003
                    f"Header '{header_name}' value is too long "
                    f"({len(header_value)} > {MAX_HEADER_VALUE_CHARS})"
                )
        return value

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        for tag in value:
            if "\r" in tag or "\n" in tag:
                raise ValueError("CR/LF characters are not allowed")  # noqa: TRY003
            if len(tag) > MAX_TAG_CHARS:
                raise ValueError("Tag is too long")  # noqa: TRY003
        return value


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
    delayed_size: int
    dlq_size: int
