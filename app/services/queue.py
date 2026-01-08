import redis.asyncio as redis
from app.config import settings
import json
import logging

logger = logging.getLogger(__name__)


class QueueService:
    """Service for managing email queue with Redis"""
    
    def __init__(self):
        self.redis_client = None
        self.queue_key = "email:queue"
        self.processing_key = "email:processing"
        self.dlq_key = "email:dlq"
    
    async def connect(self):
        """Connect to Redis"""
        self.redis_client = await redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True
        )
        logger.info("Connected to Redis")
    
    async def disconnect(self):
        """Disconnect from Redis"""
        if self.redis_client:
            await self.redis_client.close()
            logger.info("Disconnected from Redis")
    
    async def enqueue(self, email_id: str):
        """Add email to the queue"""
        await self.redis_client.lpush(self.queue_key, email_id)
        logger.info(f"Enqueued email {email_id}")
    
    async def dequeue(self) -> str:
        """Get next email from queue (blocking)"""
        # Use BRPOPLPUSH for atomicity - move from queue to processing
        result = await self.redis_client.brpoplpush(
            self.queue_key,
            self.processing_key,
            timeout=5
        )
        if result:
            logger.info(f"Dequeued email {result}")
        return result
    
    async def complete(self, email_id: str):
        """Remove email from processing set after successful send"""
        await self.redis_client.lrem(self.processing_key, 1, email_id)
        logger.info(f"Completed email {email_id}")
    
    async def move_to_dlq(self, email_id: str, error: str):
        """Move failed email to Dead Letter Queue"""
        await self.redis_client.lrem(self.processing_key, 1, email_id)
        dlq_item = json.dumps({"email_id": email_id, "error": error})
        await self.redis_client.lpush(self.dlq_key, dlq_item)
        logger.error(f"Moved email {email_id} to DLQ: {error}")
    
    async def requeue(self, email_id: str):
        """Move email back to queue for retry"""
        await self.redis_client.lrem(self.processing_key, 1, email_id)
        await self.redis_client.lpush(self.queue_key, email_id)
        logger.info(f"Requeued email {email_id} for retry")
    
    async def get_queue_size(self) -> int:
        """Get current queue size"""
        return await self.redis_client.llen(self.queue_key)
    
    async def get_processing_size(self) -> int:
        """Get number of emails being processed"""
        return await self.redis_client.llen(self.processing_key)
    
    async def get_dlq_size(self) -> int:
        """Get Dead Letter Queue size"""
        return await self.redis_client.llen(self.dlq_key)


# Global queue service instance
queue_service = QueueService()
