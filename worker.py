import asyncio
import logging
import json
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.config import settings
from app.services.queue import queue_service
from app.services.smtp import smtp_service
from app.services.email import email_service
from app.schemas.email import EmailStatus
import signal
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# Graceful shutdown flag
shutdown_flag = False


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    global shutdown_flag
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    shutdown_flag = True


async def process_email(db: AsyncSession, email_id: str) -> bool:
    """
    Process a single email from the queue
    
    Args:
        db: Database session
        email_id: Email ID to process
        
    Returns:
        bool: True if successful, False if failed
    """
    try:
        # Get email record
        email = await email_service.get_by_id(db, email_id)
        if not email:
            logger.error(f"Email {email_id} not found in database")
            await queue_service.complete(email_id)
            return False
        
        # Check if already sent
        if email.status == EmailStatus.SENT:
            logger.info(f"Email {email_id} already sent, skipping")
            await queue_service.complete(email_id)
            return True
        
        # Check retry limit
        if email.retry_count >= settings.max_retries:
            logger.error(f"Email {email_id} exceeded max retries ({settings.max_retries})")
            await email_service.update_status(
                db, email_id, EmailStatus.DLQ,
                error_message=f"Exceeded max retries ({settings.max_retries})"
            )
            await queue_service.move_to_dlq(
                email_id,
                f"Exceeded max retries: {email.error_message}"
            )
            return False
        
        # Update status to sending
        await email_service.update_status(db, email_id, EmailStatus.SENDING)
        
        # Parse addresses from JSON with error handling
        try:
            to_addresses = json.loads(email.to_addresses)
            cc_addresses = json.loads(email.cc_addresses) if email.cc_addresses else None
            bcc_addresses = json.loads(email.bcc_addresses) if email.bcc_addresses else None
        except json.JSONDecodeError as e:
            error_message = f"Invalid JSON in email address fields for email {email_id}"
            logger.exception(error_message)
            await email_service.update_status(
                db,
                email_id,
                EmailStatus.DLQ,
                error_message=error_message,
            )
            await queue_service.move_to_dlq(email_id, error_message)
            return False
        
        # Send email via SMTP
        logger.info(f"Sending email {email_id} (retry: {email.retry_count})")
        await smtp_service.send_email(
            from_address=email.from_address,
            envelope_from=email.envelope_from,
            to_addresses=to_addresses,
            cc_addresses=cc_addresses,
            bcc_addresses=bcc_addresses,
            subject=email.subject,
            body=email.body,
            is_html=bool(email.is_html)
        )
        
        # Update status to sent
        await email_service.update_status(db, email_id, EmailStatus.SENT)
        await queue_service.complete(email_id)
        
        logger.info(f"Successfully sent email {email_id}")
        return True
        
    except Exception as e:
        error_msg = str(e)
        logger.exception(f"Error processing email {email_id}")
        
        # Update status to failed and increment retry count
        await email_service.update_status(
            db, email_id, EmailStatus.FAILED,
            error_message=error_msg,
            increment_retry=True
        )
        
        # Requeue for retry (next attempt will check retry limit at the start)
        # Note: In production, use Valkey ZADD with timestamp for delayed retry
        await queue_service.requeue(email_id)
        
        return False


async def worker():
    """Main worker loop"""
    global shutdown_flag
    
    # Create database engine and session factory
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        future=True
    )
    AsyncSessionLocal = async_sessionmaker(
        engine,
        expire_on_commit=False
    )
    
    logger.info("Worker started")
    
    # Connect to queue
    await queue_service.connect()
    
    try:
        while not shutdown_flag:
            try:
                # Dequeue next email (blocking with timeout)
                email_id = await queue_service.dequeue()
                
                if email_id:
                    # Process email
                    async with AsyncSessionLocal() as db:
                        await process_email(db, email_id)
                        
            except asyncio.CancelledError:
                logger.info("Worker task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in worker loop: {str(e)}")
                await asyncio.sleep(5)  # Wait before retrying
                
    finally:
        # Cleanup
        await queue_service.disconnect()
        await engine.dispose()
        logger.info("Worker stopped")


def main():
    """Entry point for worker"""
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run worker
    try:
        asyncio.run(worker())
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user")
    except Exception:
        logger.exception("Worker error")
        sys.exit(1)


if __name__ == "__main__":
    main()
