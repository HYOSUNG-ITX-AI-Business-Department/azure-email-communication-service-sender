import time
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.mark.asyncio
async def test_dequeue_returns_email_id():
    """Test dequeue returns email ID from queue"""
    service = QueueService()
    service.redis_client = AsyncMock()
    service.redis_client.blmove = AsyncMock(return_value="email-123")

    result = await service.dequeue()

    assert result == "email-123"
    service.redis_client.blmove.assert_awaited_once()


@pytest.mark.asyncio
async def test_dequeue_returns_none_on_timeout():
    """Test dequeue returns None on timeout"""
    service = QueueService()
    service.redis_client = AsyncMock()
    service.redis_client.blmove = AsyncMock(return_value=None)

    result = await service.dequeue()

    assert result is None


@pytest.mark.asyncio
async def test_dequeue_raises_redis_error():
    """Test dequeue raises redis error"""
    service = QueueService()
    service.redis_client = AsyncMock()
    service.redis_client.blmove = AsyncMock(side_effect=redis.RedisError("connection lost"))

    with pytest.raises(redis.RedisError):
        await service.dequeue()


@pytest.mark.asyncio
async def test_complete_removes_from_processing():
    """Test complete removes email from processing queue"""
    service = QueueService()
    service.redis_client = AsyncMock()

    await service.complete("email-1")

    service.redis_client.lrem.assert_awaited_once_with(service.processing_key, 1, "email-1")


@pytest.mark.asyncio
async def test_complete_raises_redis_error():
    """Test complete raises redis error"""
    service = QueueService()
    service.redis_client = AsyncMock()
    service.redis_client.lrem = AsyncMock(side_effect=redis.RedisError("connection lost"))

    with pytest.raises(redis.RedisError):
        await service.complete("email-1")


@pytest.mark.asyncio
async def test_move_to_dlq_success():
    """Test move_to_dlq successfully moves email"""
    service = QueueService()
    service._move_to_dlq_script = AsyncMock(return_value=1)

    await service.move_to_dlq("email-1", "Permanent error")

    service._move_to_dlq_script.assert_awaited_once()
    kwargs = service._move_to_dlq_script.call_args.kwargs
    assert kwargs["keys"] == [service.processing_key, service.dlq_key]
    assert kwargs["args"][0] == "email-1"


@pytest.mark.asyncio
async def test_move_to_dlq_email_not_in_processing():
    """Test move_to_dlq when email not in processing queue"""
    service = QueueService()
    service._move_to_dlq_script = AsyncMock(return_value=0)

    await service.move_to_dlq("email-1", "Error")

    service._move_to_dlq_script.assert_awaited_once()


@pytest.mark.asyncio
async def test_move_to_dlq_raises_redis_error():
    """Test move_to_dlq raises redis error"""
    service = QueueService()
    service._move_to_dlq_script = AsyncMock(side_effect=redis.RedisError("connection lost"))

    with pytest.raises(redis.RedisError):
        await service.move_to_dlq("email-1", "Error")


@pytest.mark.asyncio
async def test_requeue_success():
    """Test requeue successfully moves email back to queue"""
    service = QueueService()
    service._requeue_script = AsyncMock(return_value=1)

    await service.requeue("email-1")

    service._requeue_script.assert_awaited_once()
    kwargs = service._requeue_script.call_args.kwargs
    assert kwargs["keys"] == [service.processing_key, service.queue_key]
    assert kwargs["args"][0] == "email-1"


@pytest.mark.asyncio
async def test_requeue_email_not_in_processing():
    """Test requeue when email not in processing queue"""
    service = QueueService()
    service._requeue_script = AsyncMock(return_value=0)

    await service.requeue("email-1")

    service._requeue_script.assert_awaited_once()


@pytest.mark.asyncio
async def test_requeue_raises_redis_error():
    """Test requeue raises redis error"""
    service = QueueService()
    service._requeue_script = AsyncMock(side_effect=redis.RedisError("connection lost"))

    with pytest.raises(redis.RedisError):
        await service.requeue("email-1")


@pytest.mark.asyncio
async def test_get_queue_size():
    """Test get_queue_size returns queue length"""
    service = QueueService()
    service.redis_client = AsyncMock()
    service.redis_client.llen = AsyncMock(return_value=42)

    size = await service.get_queue_size()

    assert size == 42
    service.redis_client.llen.assert_awaited_once_with(service.queue_key)


@pytest.mark.asyncio
async def test_get_queue_size_raises_redis_error():
    """Test get_queue_size raises redis error"""
    service = QueueService()
    service.redis_client = AsyncMock()
    service.redis_client.llen = AsyncMock(side_effect=redis.RedisError("connection lost"))

    with pytest.raises(redis.RedisError):
        await service.get_queue_size()


@pytest.mark.asyncio
async def test_get_processing_size():
    """Test get_processing_size returns processing queue length"""
    service = QueueService()
    service.redis_client = AsyncMock()
    service.redis_client.llen = AsyncMock(return_value=5)

    size = await service.get_processing_size()

    assert size == 5
    service.redis_client.llen.assert_awaited_once_with(service.processing_key)


@pytest.mark.asyncio
async def test_get_processing_size_raises_redis_error():
    """Test get_processing_size raises redis error"""
    service = QueueService()
    service.redis_client = AsyncMock()
    service.redis_client.llen = AsyncMock(side_effect=redis.RedisError("connection lost"))

    with pytest.raises(redis.RedisError):
        await service.get_processing_size()


@pytest.mark.asyncio
async def test_get_dlq_size():
    """Test get_dlq_size returns DLQ length"""
    service = QueueService()
    service.redis_client = AsyncMock()
    service.redis_client.llen = AsyncMock(return_value=3)

    size = await service.get_dlq_size()

    assert size == 3
    service.redis_client.llen.assert_awaited_once_with(service.dlq_key)


@pytest.mark.asyncio
async def test_get_dlq_size_raises_redis_error():
    """Test get_dlq_size raises redis error"""
    service = QueueService()
    service.redis_client = AsyncMock()
    service.redis_client.llen = AsyncMock(side_effect=redis.RedisError("connection lost"))

    with pytest.raises(redis.RedisError):
        await service.get_dlq_size()


@pytest.mark.asyncio
async def test_increment_db_error_count_returns_count():
    """Test increment_db_error_count returns incremented count"""
    service = QueueService()
    service.redis_client = AsyncMock()
    service.redis_client.incr = AsyncMock(return_value=3)

    count = await service.increment_db_error_count("email-1")

    assert count == 3
    expected_key = f"{service.db_error_key_prefix}:email-1"
    service.redis_client.incr.assert_awaited_once_with(expected_key)


@pytest.mark.asyncio
async def test_increment_db_error_count_raises_redis_error():
    """Test increment_db_error_count raises redis error"""
    service = QueueService()
    service.redis_client = AsyncMock()
    service.redis_client.incr = AsyncMock(side_effect=redis.RedisError("connection lost"))

    with pytest.raises(redis.RedisError):
        await service.increment_db_error_count("email-1")


@pytest.mark.asyncio
async def test_clear_db_error_count():
    """Test clear_db_error_count deletes the key"""
    service = QueueService()
    service.redis_client = AsyncMock()

    await service.clear_db_error_count("email-1")

    expected_key = f"{service.db_error_key_prefix}:email-1"
    service.redis_client.delete.assert_awaited_once_with(expected_key)


@pytest.mark.asyncio
async def test_clear_db_error_count_raises_redis_error():
    """Test clear_db_error_count raises redis error"""
    service = QueueService()
    service.redis_client = AsyncMock()
    service.redis_client.delete = AsyncMock(side_effect=redis.RedisError("connection lost"))

    with pytest.raises(redis.RedisError):
        await service.clear_db_error_count("email-1")


@pytest.mark.asyncio
async def test_connect_registers_scripts():
    """Test connect registers Lua scripts"""
    service = QueueService()
    
    with patch('app.services.queue.redis.from_url', new_callable=AsyncMock) as mock_from_url:
        mock_client = AsyncMock()
        mock_client.register_script = MagicMock(return_value=AsyncMock())
        mock_from_url.return_value = mock_client
        
        await service.connect()
        
        assert service.redis_client is not None
        assert service._move_to_dlq_script is not None
        assert service._requeue_script is not None
        assert service._requeue_delayed_script is not None
        assert service._move_ready_delayed_script is not None


@pytest.mark.asyncio
async def test_disconnect_closes_client():
    """Test disconnect closes redis client"""
    service = QueueService()
    service.redis_client = AsyncMock()
    
    await service.disconnect()
    
    service.redis_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnect_with_no_client():
    """Test disconnect handles None client gracefully"""
    service = QueueService()
    service.redis_client = None
    
    # Should not raise
    await service.disconnect()


@pytest.mark.asyncio
async def test_requeue_delayed_returns_zero_if_not_found():
    """Test requeue_delayed when email not in processing queue"""
    service = QueueService()
    service._requeue_delayed_script = AsyncMock(return_value=0)

    await service.requeue_delayed("email-1", 30)

    service._requeue_delayed_script.assert_awaited_once()


@pytest.mark.asyncio
async def test_move_ready_delayed_returns_zero_when_no_emails():
    """Test move_ready_delayed returns 0 when no emails ready"""
    service = QueueService()
    service._move_ready_delayed_script = AsyncMock(return_value=0)

    with patch('app.services.queue.time.time', return_value=1000.0):
        moved = await service.move_ready_delayed(max_batch=50)

    assert moved == 0


@pytest.mark.asyncio
async def test_move_ready_delayed_with_custom_batch_size():
    """Test move_ready_delayed respects custom batch size"""
    service = QueueService()
    service._move_ready_delayed_script = AsyncMock(return_value=25)

    with patch('app.services.queue.time.time', return_value=1000.0):
        moved = await service.move_ready_delayed(max_batch=25)

    kwargs = service._move_ready_delayed_script.call_args.kwargs
    assert kwargs["args"][1] == 25
    assert moved == 25
