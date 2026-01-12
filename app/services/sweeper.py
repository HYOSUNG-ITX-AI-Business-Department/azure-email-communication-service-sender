import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email import EmailRecord
from app.schemas.email import EmailStatus
from app.services.queue import queue_service

logger = logging.getLogger(__name__)


SWEEPER_REENQUEUE_TOTAL_LOG_EVERY = 100
SWEEPER_FAILED_TOTAL_LOG_EVERY = 100

sweeper_requeued_total = Counter(
    "sweeper_requeued_total",
    "Total number of emails re-enqueued by the sweeper",
)
sweeper_skipped_total = Counter(
    "sweeper_skipped_total",
    "Total number of emails skipped by the sweeper (already queued)",
)
sweeper_errored_total = Counter(
    "sweeper_errored_total",
    "Total number of errors encountered by the sweeper",
)
sweeper_failed_total = Counter(
    "sweeper_failed_total",
    "Total number of emails marked failed by the sweeper (max attempts exceeded)",
)
sweeper_last_success_timestamp = Gauge(
    "sweeper_last_success_timestamp",
    "Unix timestamp of the last successful sweeper iteration",
)
sweeper_sweep_duration_seconds = Histogram(
    "sweeper_sweep_duration_seconds",
    "Duration of a sweeper sweep_once execution in seconds",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)


class SweeperService:
    """Reconciles DB email state vs Redis queue mutations.

    DB and Redis are not transactional together; during partial failures an email can remain
    in PENDING/QUEUED but not exist in Redis queue. This sweeper periodically re-enqueues
    missing work safely (at-least-once), leveraging queued-set membership.
    """

    def __init__(
        self,
        *,
        grace_seconds: int | None = None,
        batch_size: int | None = None,
        interval_seconds: int | None = None,
        max_requeue_attempts: int | None = None,
    ) -> None:
        self.grace_seconds = grace_seconds if grace_seconds is not None else 60
        self.batch_size = batch_size if batch_size is not None else 100
        self.interval_seconds = interval_seconds if interval_seconds is not None else 60
        self.max_requeue_attempts = (
            max_requeue_attempts if max_requeue_attempts is not None else 10
        )

        # In-memory counters (process-local). Also mirrored as Prometheus counters.
        self._requeued_total = 0
        self._skipped_total = 0
        self._errored_total = 0
        self._failed_total = 0

    def _cutoff(self) -> datetime:
        return datetime.now(timezone.utc) - timedelta(seconds=self.grace_seconds)

    async def sweep_once(self, db: AsyncSession) -> int:
        """Run a single sweep iteration.

        Returns:
            int: number of email IDs re-enqueued in this sweep
        """
        start = time.monotonic()
        succeeded = False
        try:
            cutoff = self._cutoff()

            # NOTE: EmailRecord.updated_at uses server_default; for SQLite it's still populated.
            stmt = (
                select(EmailRecord)
                .where(
                    EmailRecord.status.in_(
                        [EmailStatus.PENDING.value, EmailStatus.QUEUED.value]
                    )
                )
                .where(EmailRecord.updated_at <= cutoff)
                .order_by(EmailRecord.updated_at.asc())
                .limit(self.batch_size)
            )

            result = await db.execute(stmt)
            records = list(result.scalars().all())

            if not records:
                succeeded = True
                return 0

            requeued = 0
            for record in records:
                email_id = record.id
                try:
                    queued = await queue_service.is_queued(email_id)
                except Exception:
                    self._errored_total += 1
                    sweeper_errored_total.inc()
                    logger.exception("Sweeper: failed to check queued-set for %s", email_id)
                    continue

                if queued:
                    self._skipped_total += 1
                    sweeper_skipped_total.inc()
                    continue

                sweeper_requeue_count = record.sweeper_requeue_count or 0
                if sweeper_requeue_count >= self.max_requeue_attempts:
                    self._failed_total += 1
                    sweeper_failed_total.inc()
                    logger.warning(
                        "Sweeper: email %s exceeded max sweeper requeue attempts (%s); marking FAILED",
                        email_id,
                        self.max_requeue_attempts,
                    )
                    failure_reason = (
                        f"max sweeper requeue attempts exceeded: {self.max_requeue_attempts}"
                    )
                    try:
                        async with db.begin():
                            record.status = EmailStatus.FAILED.value
                            record.error_message = failure_reason
                    except Exception as e:
                        self._errored_total += 1
                        sweeper_errored_total.inc()
                        try:
                            record.error_message = f"{failure_reason}; persist error: {e}"
                        except Exception:
                            pass
                        logger.exception(
                            "Sweeper: failed to persist FAILED status for %s",
                            email_id,
                        )
                    if self._failed_total % SWEEPER_FAILED_TOTAL_LOG_EVERY == 0:
                        logger.info(
                            "Sweeper progress: failed_total=%s requeued_total=%s skipped_total=%s errored_total=%s",
                            self._failed_total,
                            self._requeued_total,
                            self._skipped_total,
                            self._errored_total,
                        )
                    continue

                try:
                    await queue_service.enqueue(email_id)
                except Exception:
                    self._errored_total += 1
                    sweeper_errored_total.inc()
                    logger.exception("Sweeper: failed to enqueue %s", email_id)
                    continue

                # Best-effort: keep DB state aligned with queue mutation.
                # If DB commit fails, sweeper will retry later (idempotent).
                try:
                    async with db.begin():
                        record.status = EmailStatus.QUEUED.value
                        record.sweeper_requeue_count = sweeper_requeue_count + 1
                except Exception:
                    self._errored_total += 1
                    sweeper_errored_total.inc()
                    logger.exception("Sweeper: failed to update DB status for %s", email_id)
                    # Do not undo enqueue; at-least-once semantics.
                    continue

                requeued += 1
                self._requeued_total += 1
                sweeper_requeued_total.inc()

                if self._requeued_total % SWEEPER_REENQUEUE_TOTAL_LOG_EVERY == 0:
                    logger.info(
                        "Sweeper progress: failed_total=%s requeued_total=%s skipped_total=%s errored_total=%s",
                        self._failed_total,
                        self._requeued_total,
                        self._skipped_total,
                        self._errored_total,
                    )

            if requeued:
                logger.info(
                    "Sweeper: requeued=%s (batch_size=%s cutoff=%s)",
                    requeued,
                    self.batch_size,
                    cutoff.isoformat(),
                )
            succeeded = True
            return requeued
        finally:
            sweeper_sweep_duration_seconds.observe(time.monotonic() - start)
            if succeeded:
                sweeper_last_success_timestamp.set(time.time())

    async def run_forever(self, session_factory) -> None:
        """Run sweeper loop until cancelled."""
        logger.info(
            "Sweeper started (interval=%ss grace=%ss batch=%s max_requeue_attempts=%s)",
            self.interval_seconds,
            self.grace_seconds,
            self.batch_size,
            self.max_requeue_attempts,
        )
        backoff_seconds = float(self.interval_seconds)
        max_backoff_seconds = max(60.0, float(self.interval_seconds) * 16.0)

        while True:
            try:
                async with session_factory() as db:
                    await self.sweep_once(db)
                backoff_seconds = float(self.interval_seconds)
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                jitter = 0.5 + random.random()  # 0.5..1.5
                jittered_sleep = backoff_seconds * jitter
                logger.exception(
                    "Sweeper loop error; backing off for %ss (jittered=%ss)",
                    backoff_seconds,
                    round(jittered_sleep, 3),
                )
                await asyncio.sleep(jittered_sleep)
                backoff_seconds = min(max_backoff_seconds, backoff_seconds * 2.0)