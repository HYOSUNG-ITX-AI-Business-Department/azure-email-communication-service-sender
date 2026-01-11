import aiosmtplib
import base64
import binascii
import re
from email import encoders
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from html import unescape
import logging
from app.config import settings

logger = logging.getLogger(__name__)


class SMTPService:
    """Service for sending emails via SMTP"""
    
    async def send_email(
        self,
        from_address: str,
        envelope_from: str,
        to_addresses: list[str],
        subject: str,
        body: str,
        *,
        cc_addresses: list[str] | None = None,
        bcc_addresses: list[str] | None = None,
        reply_to: str | None = None,
        headers: dict[str, str] | None = None,
        attachments: list[dict[str, str]] | None = None,
        is_html: bool = False,
    ) -> None:
        """
        Send email via ACS Email SMTP Relay
        
        Args:
            from_address: Header From (RFC 5322.From)
            envelope_from: Envelope Sender (RFC 5321.MailFrom) 
            to_addresses: List of recipients
            subject: Email subject
            body: Email body
            cc_addresses: List of CC recipients
            bcc_addresses: List of BCC recipients
            reply_to: Reply-To address
            headers: Custom headers (allowlist enforced)
            attachments: List of attachment metadata dicts
            is_html: Whether body is HTML
        """
        # Create message body
        if is_html:
            alternative = MIMEMultipart('alternative')
            # Plain-text fallback for clients that do not render HTML.
            plain_text = unescape(re.sub(r"<[^>]+>", "", body))
            alternative.attach(MIMEText(plain_text, 'plain', 'utf-8'))
            alternative.attach(MIMEText(body, 'html', 'utf-8'))
            body_part = alternative
        else:
            body_part = MIMEText(body, 'plain', 'utf-8')

        # Attach files when present
        if attachments:
            msg = MIMEMultipart('mixed')
            msg.attach(body_part)
            for attachment in attachments:
                filename = attachment.get("filename")
                content_base64 = attachment.get("content_base64")
                content_type = attachment.get("content_type") or "application/octet-stream"

                if not filename or not content_base64:
                    raise ValueError("Attachment requires filename and content_base64")

                try:
                    payload = base64.b64decode(content_base64, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise ValueError("Attachment content_base64 is invalid") from exc

                main_type, _, sub_type = content_type.partition("/")
                if not main_type or not sub_type:
                    main_type, sub_type = "application", "octet-stream"

                part = MIMEBase(main_type, sub_type)
                part.set_payload(payload)
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    "attachment",
                    filename=filename,
                )
                msg.attach(part)
        else:
            msg = body_part
        
        # Set headers - use from_address for Header From (RFC 5322)
        msg['From'] = from_address
        msg['To'] = ', '.join(to_addresses)
        msg['Subject'] = subject
        
        if cc_addresses:
            msg['Cc'] = ', '.join(cc_addresses)
        if reply_to:
            msg['Reply-To'] = reply_to
        if headers:
            for header_name, header_value in headers.items():
                msg[header_name] = header_value
        
        # Prepare all recipients (including BCC, but don't add to headers)
        all_recipients = to_addresses.copy()
        if cc_addresses:
            all_recipients.extend(cc_addresses)
        if bcc_addresses:
            all_recipients.extend(bcc_addresses)
        
        recipient_count = len(all_recipients)
        recipient_domains = sorted(
            {addr.split("@", 1)[1] for addr in all_recipients if "@" in addr}
        )
        logger.info(
            "Sending email from %s (envelope: %s), recipients: %d, domains: %s",
            from_address,
            envelope_from,
            recipient_count,
            recipient_domains,
        )
        
        # Connect to SMTP server with STARTTLS
        try:
            # Use envelope_from for MAIL FROM command (RFC 5321)
            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_username,
                password=settings.smtp_password,
                sender=envelope_from,  # This sets the MAIL FROM (envelope sender)
                recipients=all_recipients,
                start_tls=True,  # Force STARTTLS
                validate_certs=True,
                timeout=30
            )
            
            logger.info("Email sent successfully to %d recipients", recipient_count)
            
        except aiosmtplib.SMTPException:
            logger.exception("SMTP error sending email")
            raise
        except Exception:
            logger.exception("Unexpected error sending email")
            raise


# Global SMTP service instance
smtp_service = SMTPService()
