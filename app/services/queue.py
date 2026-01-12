from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING
import asyncio

import redis.asyncio as redis

from app.config import settings

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class QueueService:
    """Service for managing email queue with Redis"""

    MOVE_TO_DLQ_SCRIPT = """
    local processing_key = KEYS[1]
    local dlq_key = KEYS[2]
    local email_id = ARGV[1]
    local dlq_item = ARGV[2]

    local removed = redis.call('LREM', processing_key, 1, email_id)
    if removed > 0 then
        redis.call('LPUSH', dlq_key, dlq_item)
        return 1
    end
    return 0
    """

    REQUEUE_SCRIPT = """
    local processing_key = KEYS[1]
    local queue_key = KEYS[2]
    local email_id = ARGV[1]

    local removed = redis.call('LREM', processing_key, 1, email_id)
    if removed > 0 then
        redis.call('LPUSH', queue_key, email_id)
        return 1
    end
    return 0
    """

    REQUEUE_DELAYED_SCRIPT = """
    local processing_key = KEYS[1]
    local delayed_key = KEYS[2]
    local email_id = ARGV[1]
    local score = tonumber(ARGV[2])

    local removed = redis.call('LREM', processing_key, 1, email_id)
    if removed > 0 then
        redis.call('ZADD', delayed_key, score, email_id)
        return 1
    end
    return 0
    """

    MOVE_READY_DELAYED_SCRIPT = """
    local delayed_key = KEYS[1]
    local queue_key = KEYS[2]
    local now = tonumber(ARGV[1])
    local max_batch = tonumber(ARGV[2])

    local email_ids = redis.call('ZRANGEBYSCORE', delayed_key, 0, now, 'LIMIT', 0, max_batch)
    if #email_ids == 0 then
        return 0
    end

    redis.call('ZREM', delayed_key, unpack(email_ids))
    for _, email_id in ipairs(email_ids) do
        redis.call('LPUSH', queue_key, email_id)
    end

    return #email_ids
    """
    
    def __init__(self) -> None:
        self.redis_client = None
        self.queue_key = "email:queue"
        self.processing_key = "email:processing"
        self.dlq_key = "email:dlq"
        self.delayed_queue_key = "email:delayed"
        # Tracks IDs that are expected to be queued (or delayed) to support the sweeper job.
        self.queued_set_key = "email:queued:set"
        self.db_error_key_prefix = "email:db-error"
        self._move_to_dlq_script = None
        self._requeue_script = None
        self._requeue_delayed_script = None
        self._move_ready_delayed_script = None

    def _db_error_key(self, email_id: str) -> str:
        return f"{self.db_error_key_prefix}:{email_id}"
    
    async def connect(self):
        """Connect to Redis"""
        self.redis_client = await redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True
        )
        self._move_to_dlq_script = self.redis_client.register_script(
            self.MOVE_TO_DLQ_SCRIPT
        )
        self._requeue_script = self.redis_client.register_script(
            self.REQUEUE_SCRIPT
        )
        self._requeue_delayed_script = self.redis_client.register_script(
            self.REQUEUE_DELAYED_SCRIPT
        )
        self._move_ready_delayed_script = self.redis_client.register_script(
            self.MOVE_READY_DELAYED_SCRIPT
        )
        logger.info("Connected to Redis")
    
    async def disconnect(self):
        """Disconnect from Redis"""
        if self.redis_client:
            await self.redis_client.close()
            logger.info("Disconnected from Redis")
    
    async def enqueue(self, email_id: str):
        """Add email to the queue.

        Also records the id in a set to support reconciliation sweeps.

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        try:
            # Keep unit tests simple (they mock lpush directly) while still supporting
            # sweeper reconciliation. We accept the small risk of the marker update
            # failing separately (at-least-once semantics still hold via sweeper).
            await self.redis_client.lpush(self.queue_key, email_id)
            await self.redis_client.sadd(self.queued_set_key, email_id)
            logger.info("Enqueued email %s", email_id)
        except redis.RedisError:
            logger.exception("Failed to enqueue email %s", email_id)
            raise

    async def increment_db_error_count(self, email_id: str) -> int:
        """Increment the DB error counter for an email.

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        key = self._db_error_key(email_id)
        try:
            count = await self.redis_client.incr(key)
        except redis.RedisError:
            logger.exception(
                "Failed to increment DB error counter for email %s",
                email_id,
            )
            raise
        return int(count)

    async def clear_db_error_count(self, email_id: str) -> None:
        """Clear the DB error counter for an email.

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        key = self._db_error_key(email_id)
        try:
            await self.redis_client.delete(key)
        except redis.RedisError:
            logger.exception(
                "Failed to clear DB error counter for email %s",
                email_id,
            )
            raise
    
    async def dequeue(self) -> str:
        """Get next email from queue (blocking).

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        # Use BLMOVE for atomicity - move from queue to processing
        try:
            result = await self.redis_client.blmove(
                self.queue_key,
                self.processing_key,
                timeout=5,
                src="RIGHT",
                dest="LEFT",
            )
        except redis.RedisError:
            logger.exception(
                "Failed to dequeue email from %s to %s",
                self.queue_key,
                self.processing_key,
            )
            raise
        if result:
            logger.info("Dequeued email %s", result)
        return result
    
    async def complete(self, email_id: str):
        """Remove email from processing set after successful send.

        Also clears queued marker (best-effort) because the work is no longer queued.

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        try:
            await self.redis_client.lrem(self.processing_key, 1, email_id)
        except redis.RedisError:
            logger.exception("Failed to complete email %s", email_id)
            raise
        await self.clear_queued(email_id)
        logger.info("Completed email %s", email_id)
    
    async def move_to_dlq(
        self,
        email_id: str,
        error: str,
        *,
        retry_count: int | None = None,
    ):
        """Move failed email to Dead Letter Queue.

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        dlq_payload: dict[str, object] = {
            "email_id": email_id,
            "error": error,
            "dlq_at": datetime.now(timezone.utc).isoformat(),
        }
        if retry_count is not None:
            dlq_payload["retry_count"] = retry_count
        dlq_item = json.dumps(dlq_payload)
        try:
            moved = await self._move_to_dlq_script(
                keys=[self.processing_key, self.dlq_key],
                args=[email_id, dlq_item],
            )
        except redis.RedisError:
            logger.exception("Failed to move email %s to DLQ", email_id)
            raise
        moved = int(moved or 0)
        if moved == 0:
            logger.warning(
                "Email %s not found in processing queue for DLQ move",
                email_id,
            )
            return
        logger.error("Moved email %s to DLQ: %s", email_id, error)
    
    async def requeue(self, email_id: str):
        """Move email back to queue for retry.

        Keeps the queued marker for reconciliation.

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        try:
            moved = await self._requeue_script(
                keys=[self.processing_key, self.queue_key],
                args=[email_id],
            )
        except redis.RedisError:
            logger.exception("Failed to requeue email %s", email_id)
            raise
        moved = int(moved or 0)
        if moved > 0:
            await self.mark_queued(email_id)
        if moved == 0:
            logger.warning(
                "Email %s not found in processing queue for requeue",
                email_id,
            )
            return
        logger.info("Requeued email %s for retry", email_id)

    async def requeue_delayed(self, email_id: str, delay_seconds: int):
        """Move email to delayed retry queue.

        Keeps the queued marker for reconciliation.

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        score = time.time() + delay_seconds
        try:
            moved = await self._requeue_delayed_script(
                keys=[self.processing_key, self.delayed_queue_key],
                args=[email_id, score],
            )
        except redis.RedisError:
            logger.exception(
                "Failed to requeue email %s with delay %s",
                email_id,
                delay_seconds,
            )
            raise
        moved = int(moved or 0)
        if moved > 0:
            await self.mark_queued(email_id)
        if moved == 0:
            logger.warning(
                "Email %s not found in processing queue for delayed requeue",
                email_id,
            )
            return
        logger.info(
            "Requeued email %s for retry in %s seconds",
            email_id,
            delay_seconds,
        )

    async def move_ready_delayed(self, max_batch: int = 100) -> int:
        """Move ready delayed emails back to main queue.

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        now = time.time()
        try:
            moved = await self._move_ready_delayed_script(
                keys=[self.delayed_queue_key, self.queue_key],
                args=[now, max_batch],
            )
        except redis.RedisError:
            logger.exception("Failed to move delayed emails back to queue")
            raise
        moved = int(moved or 0)

        if moved > 0:
            logger.info("Moved %d delayed emails back to queue", moved)
        return moved
    
    async def get_queue_size(self) -> int:
        """Get current queue size.

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        try:
            return await self.redis_client.llen(self.queue_key)
        except redis.RedisError:
            logger.exception("Failed to get queue size")
            raise
    
    async def get_processing_size(self) -> int:
        """Get number of emails being processed.

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        try:
            return await self.redis_client.llen(self.processing_key)
        except redis.RedisError:
            logger.exception("Failed to get processing queue size")
            raise
    
    async def get_dlq_size(self) -> int:
        """Get Dead Letter Queue size.

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        try:
            return await self.redis_client.llen(self.dlq_key)
        except redis.RedisError:
            logger.exception("Failed to get DLQ size")
            raise

    async def get_delayed_size(self) -> int:
        """Get delayed retry queue size.

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        try:
            return await self.redis_client.zcard(self.delayed_queue_key)
        except redis.RedisError:
            logger.exception("Failed to get delayed queue size")
            raise

    async def is_queued(self, email_id: str) -> bool:
        """Check whether an email id is tracked as queued.

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        try:
            return bool(
                await self.redis_client.sismember(self.queued_set_key, email_id)
            )
        except redis.RedisError:
            logger.exception("Failed to check queued-set membership for %s", email_id)
            raise

    async def mark_queued(self, email_id: str) -> None:
        """Mark an email id as queued (best-effort)."""
        if self.redis_client is None:
            return
        try:
            await self.redis_client.sadd(self.queued_set_key, email_id)
        except redis.RedisError:
            logger.exception("Failed to mark %s as queued", email_id)

    async def clear_queued(self, email_id: str) -> None:
        """Clear queued marker (best-effort)."""
        if self.redis_client is None:
            return
        try:
            await self.redis_client.srem(self.queued_set_key, email_id)
        except redis.RedisError:
            logger.exception("Failed to clear queued marker for %s", email_id)


# Global queue service instance
queue_service = QueueService()


class RedisLock:
    """Simple Redis distributed lock with token-safe release.

    Uses:
      - Acquire: SET key token NX EX ttl
      - Release: Lua script deletes key only if stored token matches
    """

    _RELEASE_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
else
  return 0
end
"""

    def __init__(self, redis: Redis, *, key_prefix: str = "email:lock:") -> None:
        self._redis = redis
        self._key_prefix = key_prefix
        self._release_script = None
        self._release_script_lock = asyncio.Lock()

    async def acquire(self, name: str, *, token: str, ttl_seconds: int) -> bool:
        key = f"{self._key_prefix}{name}"
        # redis-py: set(name, value, ex=ttl, nx=True) -> bool|None
        result = await self._redis.set(key, token, ex=ttl_seconds, nx=True)
        return bool(result)

    async def release(self, name: str, *, token: str) -> bool:
        key = f"{self._key_prefix}{name}"
        register_script = getattr(self._redis, "register_script", None)
        if register_script is not None:
            if self._release_script is None:
                async with self._release_script_lock:
                    if self._release_script is None:
                        self._release_script = register_script(self._RELEASE_LUA)
            deleted = await self._release_script(keys=[key], args=[token])
            return bool(deleted)

        deleted = await self._redis.eval(self._RELEASE_LUA, 1, key, token)
        return bool(deleted)