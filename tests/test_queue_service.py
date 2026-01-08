import time
from unittest.mock import AsyncMock

import pytest
import redis

from app.services.queue import QueueService


@pytest.mark.asyncio
async def test_enqueue_calls_lpush():
    service = QueueService()
    service.redis_client = AsyncMock()

    await service.enqueue("email-1")

    service.redis_client.lpush.assert_awaited_once_with(service.queue_key, "email-1")


@pytest.mark.asyncio
async def test_enqueue_raises_redis_error():
    service = QueueService()
    service.redis_client = AsyncMock()
    service.redis_client.lpush = AsyncMock(side_effect=redis.RedisError("boom"))

    with pytest.raises(redis.RedisError):
        await service.enqueue("email-1")


@pytest.mark.asyncio
async def test_requeue_delayed_uses_delay_seconds(monkeypatch):
    service = QueueService()
    service._requeue_delayed_script = AsyncMock(return_value=1)

    monkeypatch.setattr(time, "time", lambda: 1000.0)

    await service.requeue_delayed("email-1", 30)

    kwargs = service._requeue_delayed_script.call_args.kwargs
    assert kwargs["keys"] == [service.processing_key, service.delayed_queue_key]
    assert kwargs["args"][0] == "email-1"
    assert kwargs["args"][1] == 1030.0


@pytest.mark.asyncio
async def test_move_ready_delayed_returns_count(monkeypatch):
    service = QueueService()
    service._move_ready_delayed_script = AsyncMock(return_value=2)

    monkeypatch.setattr(time, "time", lambda: 2000.0)

    moved = await service.move_ready_delayed(max_batch=10)

    kwargs = service._move_ready_delayed_script.call_args.kwargs
    assert kwargs["keys"] == [service.delayed_queue_key, service.queue_key]
    assert kwargs["args"][1] == 10
    assert moved == 2
