import asyncio
import contextlib
import logging
import os
import random
import secrets
import signal
import sys
from redis.exceptions import RedisError
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiosmtplib import SMTPException, SMTPResponseException

from app.config import settings
from app.schemas.email import EmailStatus
from app.services.email import email_service
from app.services.queue import RedisLock, queue_service
from app.services.smtp import smtp_service

from prometheus_client import CollectorRegistry, Counter, Gauge, multiprocess, start_http_server

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

WORKER_SEND_ATTEMPT_TOTAL = Counter(
    "email_worker_send_attempt_total",
    "Total SMTP send attempts made by the worker",
)
WORKER_RESULT_TOTAL = Counter(
    "email_worker_result_total",
    "Total emails processed by the worker, labeled by result",
    ["result"],
)
WORKER_LOCK_ACQUIRED = Counter(
    "email_worker_lock_acquired_total",
    "Total number of per-email lock acquisitions by the worker",
)
WORKER_LOCK_CONTENDED = Counter(
    "email_worker_lock_contended_total",
    "Total number of per-email lock contention events by the worker",
)
QUEUE_SIZE = Gauge(
    "email_queue_size",
    "Queue sizes by key",
    ["queue"],
    multiprocess_mode="max",
)


def signal_handler(signum, _frame):
    """Handle shutdown signals"""
    global shutdown_flag
    logger.info("Received signal %s, initiating graceful shutdown...", signum)
    shutdown_flag = True


def calculate_backoff_delay(
    retry_count: int,
    base_delay: int,
    max_delay_seconds: int = 0,
    jitter_seconds: int = 0,
) -> int:
    """Calculate exponential backoff delay in seconds."""
    delay = base_delay * (2 ** max(retry_count - 1, 0))
    if jitter_seconds > 0:
        delay += random.randint(0, jitter_seconds)
    if max_delay_seconds > 0:
        delay = min(delay, max_delay_seconds)
    return delay


def _start_worker_metrics_server() -> None:
    """Start the Prometheus metrics HTTP server for the worker, if enabled."""
    if not settings.metrics_enabled:
        return

    port = settings.worker_metrics_port
    mode = "single"

    registry: CollectorRegistry | None = None
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        mode = "multiprocess"
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        worker_index_raw = (
            os.environ.get("WORKER_INDEX") or os.environ.get("WORKER_METRICS_PORT_OFFSET")
        )
        if worker_index_raw:
            try:
                port_offset = int(worker_index_raw)
            except ValueError:
                logger.warning(
                    "Invalid WORKER_INDEX/WORKER_METRICS_PORT_OFFSET value %r; expected int",
                    worker_index_raw,
                )
            else:
                candidate = port + port_offset
                if 1 <= candidate <= 65535:
                    port = candidate
                else:
                    logger.warning(
                        "Ignoring out-of-range worker metrics port %s (base=%s offset=%s)",
                        candidate,
                        port,
                        port_offset,
                    )

    try:
        if registry is None:
            start_http_server(
                port,
                addr=settings.worker_metrics_host,
            )
        else:
            start_http_server(
                port,
                addr=settings.worker_metrics_host,
                registry=registry,
            )
    except Exception:
        logger.exception(
            "Failed to start worker Prometheus metrics server on %s:%s (mode=%s)",
            settings.worker_metrics_host,
            port,
            mode,
        )
        return

    logger.info(
        "Worker Prometheus metrics enabled on %s:%s (mode=%s)",
        settings.worker_metrics_host,
        port,
        mode,
    )


async def poll_queue_metrics(poll_interval: float = 15.0) -> None:
    """Update queue size gauges periodically."""
    while not shutdown_flag:
        try:
            QUEUE_SIZE.labels(queue="queue").set(await queue_service.get_queue_size())
            QUEUE_SIZE.labels(queue="processing").set(
                await queue_service.get_processing_size()
            )
            QUEUE_SIZE.labels(queue="delayed").set(
                await queue_service.get_delayed_size()
            )
            QUEUE_SIZE.labels(queue="dlq").set(await queue_service.get_dlq_size())
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Failed to update queue size metrics")
        await asyncio.sleep(poll_interval)


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
    token = secrets.token_urlsafe(16)
    lock = None
    acquired = False

    try:
        redis_client = getattr(queue_service, "redis_client", None)
        if redis_client is not None:
            lock = RedisLock(redis_client)
            acquired = await lock.acquire(
                email_id,
                token=token,
                ttl_seconds=int(settings.worker_lock_ttl_seconds),
            )
            if not acquired:
                logger.info("Lock contended for email %s; requeue delayed", email_id)
                WORKER_LOCK_CONTENDED.inc()
                base_delay = int(settings.worker_lock_contended_delay_seconds)
                jitter = random.randint(0, max(1, base_delay))
                await queue_service.requeue_delayed(email_id, base_delay + jitter)
                return False
            WORKER_LOCK_ACQUIRED.inc()

        # Get email record
        email = await email_service.get_by_id(db, email_id)
        if not email:
            logger.error("Email %s not found in database", email_id)
            WORKER_RESULT_TOTAL.labels(result="missing").inc()
            await queue_service.complete(email_id)
            return False
        
        # Check if already sent
        if email.status == EmailStatus.SENT.value:
            logger.info("Email %s already sent, skipping", email_id)
            WORKER_RESULT_TOTAL.labels(result="skipped").inc()
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
            WORKER_RESULT_TOTAL.labels(result="dlq").inc()
            await email_service.update_status(
                db, email_id, EmailStatus.DLQ,
                error_message=dlq_reason,
            )
            await queue_service.move_to_dlq(
                email_id,
                dlq_reason,
                retry_count=email.retry_count,
            )
            return False
        
        # NOTE: transition_fn fallback exists for rolling deploy compatibility.
        # Once all deployments include transition_to_sending, remove this branch.
        # Symbols: transition_to_sending, transition_fn, email_service, update_status, EmailStatus.SENDING
        transition_fn = getattr(email_service, "transition_to_sending", None)
        if transition_fn is not None:
            transitioned = await transition_fn(db, email_id)
            if not transitioned:
                logger.info("Email %s already being processed, skipping", email_id)
                WORKER_RESULT_TOTAL.labels(result="skipped").inc()
                await queue_service.complete(email_id)
                return False
        else:
            await email_service.update_status(db, email_id, EmailStatus.SENDING)
        
        # Load addresses and metadata
        to_addresses = email.to_addresses
        cc_addresses = email.cc_addresses
        bcc_addresses = email.bcc_addresses
        headers = email.headers
        attachments = email.attachments
        
        # Send email via SMTP
        logger.info("Sending email %s (retry: %s)", email_id, email.retry_count)
        WORKER_SEND_ATTEMPT_TOTAL.inc()
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
                WORKER_RESULT_TOTAL.labels(result="dlq").inc()
                await email_service.update_status(
                    db, email_id, EmailStatus.DLQ,
                    error_message=f"Permanent SMTP error: {error_msg}",
                )
                await queue_service.move_to_dlq(
                    email_id,
                    error_msg,
                    retry_count=email.retry_count,
                )
                return False

            updated = await email_service.update_status(
                db, email_id, EmailStatus.FAILED,
                error_message=error_msg,
                increment_retry=True
            )
            delay_seconds = calculate_backoff_delay(
                updated.retry_count,
                settings.retry_delay_seconds,
                settings.max_retry_delay_seconds,
                settings.retry_delay_jitter_seconds,
            )
            WORKER_RESULT_TOTAL.labels(result="retry").inc()
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
                settings.max_retry_delay_seconds,
                settings.retry_delay_jitter_seconds,
            )
            WORKER_RESULT_TOTAL.labels(result="retry").inc()
            await queue_service.requeue_delayed(email_id, delay_seconds)
            return False

        # Update status to sent
        await email_service.update_status(db, email_id, EmailStatus.SENT)
        await queue_service.complete(email_id)

        logger.info("Successfully sent email %s", email_id)
        WORKER_RESULT_TOTAL.labels(result="sent").inc()
        return True
        
    except RedisError:
        logger.exception("Redis error while processing email %s", email_id)
        WORKER_RESULT_TOTAL.labels(result="redis_error").inc()
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
                "Email %s exceeded max DB error retries (%s), moving to DLQ: %s",
                email_id,
                settings.max_retries,
                error_msg,
            )
            WORKER_RESULT_TOTAL.labels(result="dlq").inc()
            await queue_service.move_to_dlq(
                email_id,
                f"Exceeded max DB error retries: {error_msg}",
                retry_count=db_error_count,
            )
            return False
        retry_step = min(db_error_count, settings.max_retries)
        delay_seconds = calculate_backoff_delay(
            retry_step,
            settings.retry_delay_seconds,
            settings.max_retry_delay_seconds,
            settings.retry_delay_jitter_seconds,
        )
        logger.warning(
            "Requeuing email %s after DB error (attempt %s) in %s seconds",
            email_id,
            db_error_count,
            delay_seconds,
        )
        WORKER_RESULT_TOTAL.labels(result="retry").inc()
        await queue_service.requeue_delayed(email_id, delay_seconds)
        return False
    except Exception as e:
        error_msg = str(e)
        logger.exception("Error processing email %s", email_id)
        WORKER_RESULT_TOTAL.labels(result="error").inc()
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
                settings.max_retry_delay_seconds,
                settings.retry_delay_jitter_seconds,
            )
        except Exception:
            logger.exception(
                "Failed to update status for email %s, using default backoff",
                email_id,
            )
            delay_seconds = calculate_backoff_delay(
                1,
                settings.retry_delay_seconds,
                settings.max_retry_delay_seconds,
                settings.retry_delay_jitter_seconds,
            )

        try:
            await queue_service.requeue_delayed(email_id, delay_seconds)
        except Exception:
            logger.exception("Failed to requeue email %s after error", email_id)
        
        return False
    finally:
        if acquired and lock is not None:
            try:
                await lock.release(email_id, token=token)
            except RedisError as exc:
                logger.warning(
                    "Failed to release lock for email_id=%s: %s",
                    email_id,
                    exc,
                )
        if not db_error:
            try:
                await queue_service.clear_db_error_count(email_id)
            except RedisError as exc:
                logger.warning(
                    "Failed to clear DB error count for email_id=%s: %s",
                    email_id,
                    exc,
                )


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


async def poll_processing_reaper(
    poll_interval: float = 60.0,
    batch_size: int = 100,
) -> None:
    """Reap stuck processing emails back to queue."""
    while not shutdown_flag:
        try:
            moved = await queue_service.reap_processing(max_batch=batch_size)
            if moved == 0:
                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error reaping processing queue")
            await asyncio.sleep(poll_interval)


async def worker():
    """Main worker loop"""
    global shutdown_flag

    engine = None
    delayed_task = None
    reaper_task = None
    metrics_task = None
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

        _start_worker_metrics_server()

        # Connect to queue
        await queue_service.connect()

        delayed_task = asyncio.create_task(poll_delayed_queue())
        if settings.processing_reaper_enabled:
            reaper_task = asyncio.create_task(
                poll_processing_reaper(
                    poll_interval=float(settings.processing_reaper_interval_seconds),
                    batch_size=int(settings.processing_reaper_batch_size),
                )
            )
        worker_metrics_interval = (
            float(settings.worker_metrics_poll_interval_seconds)
            if settings.metrics_enabled
            else 0.0
        )
        if settings.metrics_enabled:
            metrics_task = asyncio.create_task(
                poll_queue_metrics(poll_interval=worker_metrics_interval)
            )

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
                if reaper_task and not reaper_task.done():
                    reaper_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await reaper_task
                if metrics_task and not metrics_task.done():
                    metrics_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await metrics_task
                await queue_service.disconnect()
                await asyncio.sleep(5)
                await queue_service.connect()
                delayed_task = asyncio.create_task(poll_delayed_queue())
                if settings.processing_reaper_enabled:
                    reaper_task = asyncio.create_task(
                        poll_processing_reaper(
                            poll_interval=float(settings.processing_reaper_interval_seconds),
                            batch_size=int(settings.processing_reaper_batch_size),
                        )
                    )
                if settings.metrics_enabled:
                    metrics_task = asyncio.create_task(
                        poll_queue_metrics(poll_interval=worker_metrics_interval)
                    )
            except Exception:
                logger.exception("Error in worker loop")
                await asyncio.sleep(5)  # Wait before retrying
                
    finally:
        # Cleanup
        if delayed_task is not None:
            delayed_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await delayed_task
        if reaper_task is not None:
            reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reaper_task
        if metrics_task is not None:
            metrics_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await metrics_task
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