import pytest
from unittest.mock import AsyncMock
import aiosmtplib

from app.services.smtp import SMTPService


@pytest.mark.asyncio
async def test_send_email_builds_headers_and_recipients(monkeypatch):
    send_mock = AsyncMock()
    monkeypatch.setattr("app.services.smtp.aiosmtplib.send", send_mock)

    service = SMTPService()
    await service.send_email(
        from_address="from@example.com",
        envelope_from="bounce@example.com",
        to_addresses=["to@example.com"],
        cc_addresses=["cc@example.com"],
        bcc_addresses=["bcc@example.com"],
        reply_to="reply@example.com",
        headers={"X-Test": "true"},
        attachments=None,
        subject="Hello",
        body="Body",
        is_html=False,
    )

    send_mock.assert_awaited_once()
    args, kwargs = send_mock.call_args
    message = args[0]

    assert message["From"] == "from@example.com"
    assert message["To"] == "to@example.com"
    assert message["Cc"] == "cc@example.com"
    assert message["Reply-To"] == "reply@example.com"
    assert message["X-Test"] == "true"

    assert kwargs["sender"] == "bounce@example.com"
    assert set(kwargs["recipients"]) == {"to@example.com", "cc@example.com", "bcc@example.com"}


@pytest.mark.asyncio
async def test_send_email_raises_smtp_exception(monkeypatch):
    send_mock = AsyncMock(side_effect=aiosmtplib.SMTPException("boom"))
    monkeypatch.setattr("app.services.smtp.aiosmtplib.send", send_mock)

    service = SMTPService()
    with pytest.raises(aiosmtplib.SMTPException):
        await service.send_email(
            from_address="from@example.com",
            envelope_from="bounce@example.com",
            to_addresses=["to@example.com"],
            subject="Hello",
            body="Body",
        )
