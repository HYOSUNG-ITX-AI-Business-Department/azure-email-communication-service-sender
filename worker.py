import asyncio
import contextlib
import logging
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.exc import OperationalError
from aiosmtplib import SMTPException, SMTPResponseException
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

# Common permanent SMTP response codes; treat others as retryable.
PERMANENT_SMTP_CODES = {500, 501, 502, 503, 504, 521, 550, 551, 552, 553, 554}

# Graceful shutdown flag
shutdown_flag = False


def signal_handler(signum, _frame):
    """Handle shutdown signals"""
    global shutdown_flag
    logger.info("Received signal %s, initiating graceful shutdown...", signum)
    shutdown_flag = True


def calculate_backoff_delay(retry_count: int, base_delay: int) -> int:
    """Calculate exponential backoff delay in seconds."""
    return base_delay * (2 ** max(retry_count - 1, 0))


async def process_email(db: AsyncSession, email_id: str) -> bool:
    """
    Process a single email from the queue
    
    Args:
        db: Database session
        email_id: Email ID to process
        
    Returns:
        bool: True if successful, False if failed
    """
    db_error = False
    try:
        # Get email record
        email = await email_service.get_by_id(db, email_id)
        if not email:
            logger.error("Email %s not found in database", email_id)
            await queue_service.complete(email_id)
            return False
        
        # Check if already sent
        if email.status == EmailStatus.SENT:
            logger.info("Email %s already sent, skipping", email_id)
            await queue_service.complete(email_id)
            return True
        
        # Check retry limit
        if email.retry_count >= settings.max_retries:
            dlq_reason = email.error_message or (
                f"Exceeded max retries ({settings.max_retries})"
            )
            logger.error(
                "Email %s exceeded max retries (%s)",
                email_id,
                settings.max_retries,
            )
            await email_service.update_status(
                db, email_id, EmailStatus.DLQ,
                error_message=dlq_reason,
            )
            await queue_service.move_to_dlq(
                email_id,
                dlq_reason,
            )
            return False
        
        # Update status to sending
        await email_service.update_status(db, email_id, EmailStatus.SENDING)
        
        # Load addresses and metadata
        to_addresses = email.to_addresses
        cc_addresses = email.cc_addresses
        bcc_addresses = email.bcc_addresses
        headers = email.headers
        attachments = email.attachments
        
        # Send email via SMTP
        logger.info("Sending email %s (retry: %s)", email_id, email.retry_count)
        try:
            await smtp_service.send_email(
                from_address=email.from_address,
                envelope_from=email.envelope_from,
                to_addresses=to_addresses,
                cc_addresses=cc_addresses,
                bcc_addresses=bcc_addresses,
                reply_to=email.reply_to,
                headers=headers,
                attachments=attachments,
                subject=email.subject,
                body=email.body,
                is_html=bool(email.is_html)
            )
        except SMTPResponseException as exc:
            error_msg = str(exc)
            logger.exception("SMTP response error for email %s", email_id)

            if exc.code and exc.code in PERMANENT_SMTP_CODES:
                await email_service.update_status(
                    db, email_id, EmailStatus.DLQ,
                    error_message=f"Permanent SMTP error: {error_msg}",
                )
                await queue_service.move_to_dlq(email_id, error_msg)
                return False

            updated = await email_service.update_status(
                db, email_id, EmailStatus.FAILED,
                error_message=error_msg,
                increment_retry=True
            )
            delay_seconds = calculate_backoff_delay(
                updated.retry_count,
                settings.retry_delay_seconds,
            )
            await queue_service.requeue_delayed(email_id, delay_seconds)
            return False
        except SMTPException as exc:
            error_msg = str(exc)
            logger.exception("SMTP error for email %s", email_id)
            updated = await email_service.update_status(
                db, email_id, EmailStatus.FAILED,
                error_message=error_msg,
                increment_retry=True
            )
            delay_seconds = calculate_backoff_delay(
                updated.retry_count,
                settings.retry_delay_seconds,
            )
            await queue_service.requeue_delayed(email_id, delay_seconds)
            return False

        # Update status to sent
        await email_service.update_status(db, email_id, EmailStatus.SENT)
        await queue_service.complete(email_id)

        logger.info("Successfully sent email %s", email_id)
        return True
        
    except RedisError:
        logger.exception("Redis error while processing email %s", email_id)
        raise
    except OperationalError as exc:
        db_error = True
        try:
            await db.rollback()
        except Exception:
            logger.exception("Failed to rollback DB session after operational error")
        error_msg = str(exc)
        logger.exception("Database error processing email %s", email_id)
        # DB session may be in a bad state; requeue for retry with backoff
        db_error_count = await queue_service.increment_db_error_count(email_id)
        if db_error_count >= settings.max_retries:
            logger.exception(
                "Email %s exceeded max DB error retries (%s), moving to DLQ",
                email_id,
                settings.max_retries,
            )
            await queue_service.move_to_dlq(
                email_id,
                f"Exceeded max DB error retries: {error_msg}",
            )
            return False
        retry_step = min(db_error_count, settings.max_retries)
        delay_seconds = calculate_backoff_delay(
            retry_step,
            settings.retry_delay_seconds,
        )
        logger.warning(
            "Requeuing email %s after DB error (attempt %s) in %s seconds",
            email_id,
            db_error_count,
            delay_seconds,
        )
        await queue_service.requeue_delayed(email_id, delay_seconds)
        return False
    except Exception as e:
        error_msg = str(e)
        logger.exception("Error processing email %s", email_id)
        try:
            await db.rollback()
        except Exception:
            logger.exception("Failed to rollback DB session after error")

        try:
            updated = await email_service.update_status(
                db, email_id, EmailStatus.FAILED,
                error_message=error_msg,
                increment_retry=True
            )
            delay_seconds = calculate_backoff_delay(
                updated.retry_count,
                settings.retry_delay_seconds,
            )
        except Exception:
            logger.exception(
                "Failed to update status for email %s, using default backoff",
                email_id,
            )
            delay_seconds = calculate_backoff_delay(1, settings.retry_delay_seconds)

        try:
            await queue_service.requeue_delayed(email_id, delay_seconds)
        except Exception:
            logger.exception("Failed to requeue email %s after error", email_id)
        
        return False
    finally:
        if not db_error:
            with contextlib.suppress(RedisError):
                await queue_service.clear_db_error_count(email_id)


async def poll_delayed_queue(poll_interval: float = 1.0, batch_size: int = 100) -> None:
    """Move ready delayed emails back to the main queue."""
    while not shutdown_flag:
        try:
            moved = await queue_service.move_ready_delayed(max_batch=batch_size)
            if moved == 0:
                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error processing delayed queue")
            await asyncio.sleep(poll_interval)


async def worker():
    """Main worker loop"""
    global shutdown_flag

    engine = None
    delayed_task = None
    AsyncSessionLocal = None

    try:
        # Create database engine and session factory
        engine = create_async_engine(
            settings.database_url,
            echo=False
        )
        AsyncSessionLocal = async_sessionmaker(
            engine,
            expire_on_commit=False
        )

        logger.info("Worker started")

        # Connect to queue
        await queue_service.connect()

        delayed_task = asyncio.create_task(poll_delayed_queue())

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
            except RedisError:
                logger.exception("Redis error in worker loop, reconnecting...")
                if delayed_task and not delayed_task.done():
                    delayed_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await delayed_task
                await queue_service.disconnect()
                await asyncio.sleep(5)
                await queue_service.connect()
                delayed_task = asyncio.create_task(poll_delayed_queue())
            except Exception:
                logger.exception("Error in worker loop")
                await asyncio.sleep(5)  # Wait before retrying
                
    finally:
        # Cleanup
        if delayed_task is not None:
            delayed_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await delayed_task
        with contextlib.suppress(Exception):
            await queue_service.disconnect()
        if engine is not None:
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
