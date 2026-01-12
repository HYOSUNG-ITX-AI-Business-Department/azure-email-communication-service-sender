import time

import pytest
import redis.asyncio as redis

from app.services.queue import QueueService


@pytest.mark.asyncio
async def test_reap_processing_moves_expired_id_back_to_queue(monkeypatch):
    # Use a dedicated QueueService instance with isolated keys.
    svc = QueueService()
    svc.queue_key = "test:email:queue"
    svc.processing_key = "test:email:processing"
    svc.processing_visibility_key = "test:email:processing:vis"
    svc.delayed_queue_key = "test:email:delayed"
    svc.dlq_key = "test:email:dlq"
    svc.queued_set_key = "test:email:queued:set"

    client = await redis.from_url("redis://localhost:6379/0", decode_responses=True)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        pytest.skip("Redis not available on localhost:6379")
    try:
        svc.redis_client = client
        svc._move_to_dlq_script = client.register_script(svc.MOVE_TO_DLQ_SCRIPT)
        svc._requeue_script = client.register_script(svc.REQUEUE_SCRIPT)
        svc._requeue_delayed_script = client.register_script(svc.REQUEUE_DELAYED_SCRIPT)
        svc._move_ready_delayed_script = client.register_script(svc.MOVE_READY_DELAYED_SCRIPT)
        svc._reap_processing_script = client.register_script(svc.REAP_PROCESSING_SCRIPT)

        # Ensure clean slate.
        await client.delete(
            svc.queue_key,
            svc.processing_key,
            svc.processing_visibility_key,
            svc.delayed_queue_key,
            svc.dlq_key,
            svc.queued_set_key,
        )

        email_id = "email-1"
        # Simulate: moved to processing but worker crashed (never completed).
        await client.lpush(svc.processing_key, email_id)
        # Mark as expired.
        await client.zadd(svc.processing_visibility_key, {email_id: time.time() - 10})

        moved = await svc.reap_processing(max_batch=10)
        assert moved == 1

        # Should be removed from processing and visibility set.
        assert await client.lrange(svc.processing_key, 0, -1) == []
        assert await client.zscore(svc.processing_visibility_key, email_id) is None

        # Should be back on main queue.
        assert await client.lrange(svc.queue_key, 0, -1) == [email_id]
    finally:
        await client.delete(
            svc.queue_key,
            svc.processing_key,
            svc.processing_visibility_key,
            svc.delayed_queue_key,
            svc.dlq_key,
            svc.queued_set_key,
        )
        await client.aclose()