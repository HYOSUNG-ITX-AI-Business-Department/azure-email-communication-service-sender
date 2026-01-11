from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiosmtplib import SMTPResponseException
from redis.exceptions import RedisError
from sqlalchemy.exc import OperationalError

import worker
from app.schemas.email import EmailStatus


def _make_email(**overrides: Any) -> SimpleNamespace:  # noqa: ANN401
    defaults = {
        "status": EmailStatus.PENDING,
        "retry_count": 0,
        "from_address": "from@example.com",
        "envelope_from": "bounce@example.com",
        "to_addresses": ["to@example.com"],
        "cc_addresses": None,
        "bcc_addresses": None,
        "reply_to": None,
        "headers": None,
        "attachments": None,
        "subject": "Hello",
        "body": "Body",
        "is_html": False,
        "error_message": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _patch_worker_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    max_retries: int = 3,
    retry_delay_seconds: int = 10,
    max_retry_delay_seconds: int = 0,
    retry_delay_jitter_seconds: int = 0,
) -> None:
    monkeypatch.setattr(
        worker,
        "settings",
        SimpleNamespace(
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
            max_retry_delay_seconds=max_retry_delay_seconds,
            retry_delay_jitter_seconds=retry_delay_jitter_seconds,
        ),
    )


@pytest.mark.asyncio
async def test_process_email_marks_sent(monkeypatch):
    email = _make_email()
    email_service = SimpleNamespace(
        get_by_id=AsyncMock(return_value=email),
        update_status=AsyncMock(return_value=email),
    )
    queue_service = SimpleNamespace(
        complete=AsyncMock(),
        move_to_dlq=AsyncMock(),
        requeue_delayed=AsyncMock(),
        increment_db_error_count=AsyncMock(return_value=1),
        clear_db_error_count=AsyncMock(),
    )
    smtp_service = SimpleNamespace(send_email=AsyncMock())

    monkeypatch.setattr(worker, "email_service", email_service)
    monkeypatch.setattr(worker, "queue_service", queue_service)
    monkeypatch.setattr(worker, "smtp_service", smtp_service)
    _patch_worker_settings(monkeypatch)

    result = await worker.process_email(AsyncMock(), "email-1")

    assert result is True
    queue_service.complete.assert_awaited_once_with("email-1")
    assert queue_service.clear_db_error_count.await_count == 1
    statuses = [call.args[2] for call in email_service.update_status.call_args_list]
    assert statuses[0] == EmailStatus.SENDING
    assert statuses[-1] == EmailStatus.SENT
    smtp_service.send_email.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_email_permanent_smtp_error_moves_to_dlq(monkeypatch):
    email = _make_email()
    monkeypatch.setattr(worker, "PERMANENT_SMTP_CODES", {550})
    email_service = SimpleNamespace(
        get_by_id=AsyncMock(return_value=email),
        update_status=AsyncMock(return_value=email),
    )
    queue_service = SimpleNamespace(
        complete=AsyncMock(),
        move_to_dlq=AsyncMock(),
        requeue_delayed=AsyncMock(),
        increment_db_error_count=AsyncMock(return_value=1),
        clear_db_error_count=AsyncMock(),
    )
    smtp_service = SimpleNamespace(
        send_email=AsyncMock(side_effect=SMTPResponseException(550, "perm"))
    )

    monkeypatch.setattr(worker, "email_service", email_service)
    monkeypatch.setattr(worker, "queue_service", queue_service)
    monkeypatch.setattr(worker, "smtp_service", smtp_service)
    _patch_worker_settings(monkeypatch)

    result = await worker.process_email(AsyncMock(), "email-1")

    assert result is False
    queue_service.move_to_dlq.assert_awaited_once()
    queue_service.requeue_delayed.assert_not_awaited()
    assert queue_service.clear_db_error_count.await_count == 1
    statuses = [call.args[2] for call in email_service.update_status.call_args_list]
    assert EmailStatus.DLQ in statuses


@pytest.mark.asyncio
async def test_process_email_transient_smtp_error_requeues(monkeypatch):
    email = _make_email()
    updated_email = _make_email(retry_count=1)
    email_service = SimpleNamespace(
        get_by_id=AsyncMock(return_value=email),
        update_status=AsyncMock(return_value=updated_email),
    )
    queue_service = SimpleNamespace(
        complete=AsyncMock(),
        move_to_dlq=AsyncMock(),
        requeue_delayed=AsyncMock(),
        increment_db_error_count=AsyncMock(return_value=1),
        clear_db_error_count=AsyncMock(),
    )
    smtp_service = SimpleNamespace(
        send_email=AsyncMock(side_effect=SMTPResponseException(450, "try later"))
    )

    monkeypatch.setattr(worker, "email_service", email_service)
    monkeypatch.setattr(worker, "queue_service", queue_service)
    monkeypatch.setattr(worker, "smtp_service", smtp_service)
    _patch_worker_settings(monkeypatch)

    result = await worker.process_email(AsyncMock(), "email-1")

    assert result is False
    queue_service.requeue_delayed.assert_awaited_once()
    queue_service.move_to_dlq.assert_not_awaited()
    statuses = [call.args[2] for call in email_service.update_status.call_args_list]
    assert EmailStatus.FAILED in statuses


@pytest.mark.asyncio
async def test_process_email_operational_error_requeues(monkeypatch):
    op_error = OperationalError("select", {}, Exception("db"))
    email_service = SimpleNamespace(
        get_by_id=AsyncMock(side_effect=op_error),
        update_status=AsyncMock(),
    )
    queue_service = SimpleNamespace(
        complete=AsyncMock(),
        move_to_dlq=AsyncMock(),
        requeue_delayed=AsyncMock(),
        increment_db_error_count=AsyncMock(return_value=2),
        clear_db_error_count=AsyncMock(),
    )
    smtp_service = SimpleNamespace(send_email=AsyncMock())

    monkeypatch.setattr(worker, "email_service", email_service)
    monkeypatch.setattr(worker, "queue_service", queue_service)
    monkeypatch.setattr(worker, "smtp_service", smtp_service)
    _patch_worker_settings(monkeypatch)

    result = await worker.process_email(AsyncMock(), "email-1")

    assert result is False
    queue_service.increment_db_error_count.assert_awaited_once_with("email-1")
    queue_service.requeue_delayed.assert_awaited_once_with("email-1", 20)
    assert queue_service.clear_db_error_count.await_count == 0


@pytest.mark.asyncio
async def test_process_email_operational_error_moves_to_dlq(monkeypatch):
    op_error = OperationalError("select", {}, Exception("db"))
    email_service = SimpleNamespace(
        get_by_id=AsyncMock(side_effect=op_error),
        update_status=AsyncMock(),
    )
    queue_service = SimpleNamespace(
        complete=AsyncMock(),
        move_to_dlq=AsyncMock(),
        requeue_delayed=AsyncMock(),
        increment_db_error_count=AsyncMock(return_value=3),
        clear_db_error_count=AsyncMock(),
    )
    smtp_service = SimpleNamespace(send_email=AsyncMock())

    monkeypatch.setattr(worker, "email_service", email_service)
    monkeypatch.setattr(worker, "queue_service", queue_service)
    monkeypatch.setattr(worker, "smtp_service", smtp_service)
    _patch_worker_settings(monkeypatch)

    result = await worker.process_email(AsyncMock(), "email-1")

    assert result is False
    queue_service.move_to_dlq.assert_awaited_once()
    queue_service.requeue_delayed.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_email_redis_error_propagates(monkeypatch):
    email = _make_email()
    email_service = SimpleNamespace(
        get_by_id=AsyncMock(return_value=email),
        update_status=AsyncMock(return_value=email),
    )
    queue_service = SimpleNamespace(
        complete=AsyncMock(side_effect=RedisError("connection lost")),
        move_to_dlq=AsyncMock(),
        requeue_delayed=AsyncMock(),
        increment_db_error_count=AsyncMock(return_value=1),
        clear_db_error_count=AsyncMock(),
    )
    smtp_service = SimpleNamespace(send_email=AsyncMock())

    monkeypatch.setattr(worker, "email_service", email_service)
    monkeypatch.setattr(worker, "queue_service", queue_service)
    monkeypatch.setattr(worker, "smtp_service", smtp_service)
    _patch_worker_settings(monkeypatch)

    with pytest.raises(RedisError):
        await worker.process_email(AsyncMock(), "email-1")

    queue_service.clear_db_error_count.assert_awaited_once_with("email-1")
