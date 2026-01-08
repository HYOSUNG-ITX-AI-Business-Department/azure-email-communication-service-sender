from sqlalchemy import Column, String, Text, DateTime, Integer, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from datetime import datetime
import uuid

Base = declarative_base()


class EmailRecord(Base):
    """Database model for email records"""
    __tablename__ = "emails"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    idempotency_key = Column(String, unique=True, nullable=True, index=True)
    
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
