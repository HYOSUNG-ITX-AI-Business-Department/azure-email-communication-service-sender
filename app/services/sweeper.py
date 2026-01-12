import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email import EmailRecord
from app.schemas.email import EmailStatus
from app.services.queue import queue_service

logger = logging.getLogger(__name__)


SWEEPER_REENQUEUE_TOTAL_LOG_EVERY = 100
SWEEPER_FAILED_TOTAL_LOG_EVERY = 100


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

        # In-memory counters (process-local; Prometheus metrics are handled in worker/main).
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
        cutoff = self._cutoff()

        # NOTE: EmailRecord.updated_at uses server_default; for SQLite it's still populated.
        stmt = (
            select(EmailRecord)
            .where(EmailRecord.status.in_([EmailStatus.PENDING.value, EmailStatus.QUEUED.value]))
            .where(EmailRecord.updated_at <= cutoff)
            .order_by(EmailRecord.updated_at.asc())
            .limit(self.batch_size)
        )

        result = await db.execute(stmt)
        records = list(result.scalars().all())

        if not records:
            return 0

        requeued = 0
        for record in records:
            email_id = record.id
            try:
                queued = await queue_service.is_queued(email_id)
            except Exception:
                self._errored_total += 1
                logger.exception("Sweeper: failed to check queued-set for %s", email_id)
                continue

            if queued:
                self._skipped_total += 1
                continue

            sweeper_requeue_count = getattr(record, "sweeper_requeue_count", 0) or 0
            if sweeper_requeue_count >= self.max_requeue_attempts:
                self._failed_total += 1
                logger.warning(
                    "Sweeper: email %s exceeded max sweeper requeue attempts (%s); marking FAILED",
                    email_id,
                    self.max_requeue_attempts,
                )
                try:
                    record.status = EmailStatus.FAILED.value
                    await db.commit()
                except Exception:
                    self._errored_total += 1
                    try:
                        await db.rollback()
                    except Exception:
                        logger.exception(
                            "Sweeper: failed to rollback after marking FAILED for %s",
                            email_id,
                        )
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
                logger.exception("Sweeper: failed to enqueue %s", email_id)
                continue

            # Best-effort: keep DB state aligned with queue mutation.
            # If DB commit fails, sweeper will retry later (idempotent).
            try:
                record.status = EmailStatus.QUEUED.value
                record.sweeper_requeue_count = sweeper_requeue_count + 1
                await db.commit()
            except Exception:
                self._errored_total += 1
                try:
                    await db.rollback()
                except Exception:
                    logger.exception("Sweeper: failed to rollback after DB error for %s", email_id)
                logger.exception("Sweeper: failed to update DB status for %s", email_id)
                # Do not undo enqueue; at-least-once semantics.
                continue

            requeued += 1
            self._requeued_total += 1

            if self._requeued_total % SWEEPER_REENQUEUE_TOTAL_LOG_EVERY == 0:
                logger.info(
                    "Sweeper progress: requeued_total=%s skipped_total=%s errored_total=%s",
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
        return requeued

    async def run_forever(self, session_factory) -> None:
        """Run sweeper loop until cancelled."""
        logger.info(
            "Sweeper started (interval=%ss grace=%ss batch=%s max_requeue_attempts=%s)",
            self.interval_seconds,
            self.grace_seconds,
            self.batch_size,
            self.max_requeue_attempts,
        )
        while True:
            try:
                async with session_factory() as db:
                    await self.sweep_once(db)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Sweeper loop error")
            await asyncio.sleep(self.interval_seconds)