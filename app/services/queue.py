import redis.asyncio as redis
from app.config import settings
import json
import logging
import time

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
    
    def __init__(self):
        self.redis_client = None
        self.queue_key = "email:queue"
        self.processing_key = "email:processing"
        self.dlq_key = "email:dlq"
        self.delayed_queue_key = "email:delayed"
        self._move_to_dlq_script = None
        self._requeue_script = None
        self._requeue_delayed_script = None
        self._move_ready_delayed_script = None
    
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

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        try:
            await self.redis_client.lpush(self.queue_key, email_id)
            logger.info("Enqueued email %s", email_id)
        except redis.RedisError:
            logger.exception("Failed to enqueue email %s", email_id)
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

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        try:
            await self.redis_client.lrem(self.processing_key, 1, email_id)
        except redis.RedisError:
            logger.exception("Failed to complete email %s", email_id)
            raise
        logger.info("Completed email %s", email_id)
    
    async def move_to_dlq(self, email_id: str, error: str):
        """Move failed email to Dead Letter Queue.

        Raises:
            redis.RedisError: When Redis operations fail.
        """
        dlq_item = json.dumps({"email_id": email_id, "error": error})
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
        if moved == 0:
            logger.warning(
                "Email %s not found in processing queue for requeue",
                email_id,
            )
            return
        logger.info("Requeued email %s for retry", email_id)

    async def requeue_delayed(self, email_id: str, delay_seconds: int):
        """Move email to delayed retry queue.

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


# Global queue service instance
queue_service = QueueService()
