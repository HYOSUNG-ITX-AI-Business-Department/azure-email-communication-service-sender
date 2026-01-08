from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, String, Text, DateTime, Integer, JSON, UniqueConstraint, Index
from sqlalchemy.sql import func
from datetime import datetime
import uuid


class Base(DeclarativeBase):
    """Base class for declarative models"""
    pass


class EmailRecord(Base):
    """Database model for email records"""
    __tablename__ = "emails"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    caller_id = Column(String, nullable=True, index=True)  # Caller identifier for multi-tenant isolation
    idempotency_key = Column(String, nullable=True, index=True)
    
    # Email addresses
    from_address = Column(String, nullable=False, index=True)
    envelope_from = Column(String, nullable=False, index=True)
    to_addresses = Column(JSON, nullable=False)  # List of recipients
    cc_addresses = Column(JSON, nullable=True)
    bcc_addresses = Column(JSON, nullable=True)
    
    # Email content
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    is_html = Column(Integer, default=0)  # SQLite doesn't have boolean
    
    # Status tracking
    status = Column(String, nullable=False, default="pending", index=True)
    retry_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    sent_at = Column(DateTime, nullable=True)
    
    # Audit trail
    audit_log = Column(JSON, nullable=True)  # List of status changes with timestamps
    
    # Composite unique constraint for multi-tenant idempotency
    __table_args__ = (
        UniqueConstraint('caller_id', 'idempotency_key', name='uix_caller_idempotency'),
        Index('ix_caller_idempotency', 'caller_id', 'idempotency_key'),
    )
