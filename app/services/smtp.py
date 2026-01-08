import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional
import logging
from app.config import settings

logger = logging.getLogger(__name__)


class SMTPService:
    """Service for sending emails via SMTP"""
    
    async def send_email(
        self,
        from_address: str,
        envelope_from: str,
        to_addresses: List[str],
        cc_addresses: Optional[List[str]],
        bcc_addresses: Optional[List[str]],
        subject: str,
        body: str,
        is_html: bool = False
    ) -> None:
        """
        Send email via ACS Email SMTP Relay
        
        Args:
            from_address: Header From (RFC 5322.From)
            envelope_from: Envelope Sender (RFC 5321.MailFrom) 
            to_addresses: List of recipients
            cc_addresses: List of CC recipients
            bcc_addresses: List of BCC recipients
            subject: Email subject
            body: Email body
            is_html: Whether body is HTML
        """
        # Create message
        msg = MIMEMultipart() if is_html else MIMEText(body, 'plain', 'utf-8')
        
        if is_html:
            msg.attach(MIMEText(body, 'html', 'utf-8'))
        
        # Set headers - use from_address for Header From (RFC 5322)
        msg['From'] = from_address
        msg['To'] = ', '.join(to_addresses)
        msg['Subject'] = subject
        
        if cc_addresses:
            msg['Cc'] = ', '.join(cc_addresses)
        
        # Prepare all recipients (including BCC, but don't add to headers)
        all_recipients = to_addresses.copy()
        if cc_addresses:
            all_recipients.extend(cc_addresses)
        if bcc_addresses:
            all_recipients.extend(bcc_addresses)
        
        logger.info(f"Sending email from {from_address} (envelope: {envelope_from}) to {all_recipients}")
        
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
            
            logger.info(f"Email sent successfully to {all_recipients}")
            
        except aiosmtplib.SMTPException as e:
            logger.error(f"SMTP error sending email: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error sending email: {str(e)}")
            raise


# Global SMTP service instance
smtp_service = SMTPService()
