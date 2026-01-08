# Azure Email Communication Service Sender

REST API service for sending emails via Azure Communication Services (ACS) Email SMTP Relay.

## Overview

This service provides a REST API that accepts email sending requests, validates and stores them, queues them for processing, and sends them via SMTP SUBMISSION to Azure Communication Services Email SMTP Relay (`smtp.azurecomm.net:587`) with STARTTLS enforcement.

### Key Features

- **Envelope Sender Separation**: Separates SMTP authentication identifier (Entra-based SMTP Username) from the sender address
  - `envelope_from`: RFC 5321 MAIL FROM (Envelope Sender)
  - `from`: RFC 5322 From header (Header From)
  - Default policy: `from == envelope_from` (aligned)
- **Allowed MailFrom List**: Only whitelisted sender addresses can be used
- **Idempotency**: Prevent duplicate email submissions using idempotency keys
- **Retry Logic**: Automatic retry with exponential backoff
- **Dead Letter Queue (DLQ)**: Failed emails moved to DLQ after max retries
- **Status Checking**: Query email status and delivery information
- **Audit Trail**: Complete audit log of status changes
- **Observability**: Structured logging for monitoring and debugging

## Architecture

The system consists of two main components:

1. **Sender Service (API)**: FastAPI-based REST API that validates, stores, and queues email requests
2. **Worker Service**: Background worker that dequeues emails and sends them via SMTP

```text
┌─────────┐    REST API     ┌────────────┐    Valkey Queue   ┌────────┐    SMTP    ┌──────────────┐
│ Client  │ ──────────────> │   Sender   │ ───────────────> │ Worker │ ─────────> │ ACS SMTP     │
│         │                 │  Service   │                   │        │            │ Relay        │
└─────────┘                 └────────────┘                   └────────┘            │ (Azure)      │
                                  │                                                 └──────────────┘
                                  v
                            ┌─────────────┐
                            │ PostgreSQL  │
                            │  Database   │
                            └─────────────┘
```

## Prerequisites

- Python 3.11+
- PostgreSQL 12+ (for production database)
- Valkey (Redis-compatible message queue)
- Azure Communication Services Email resource
- Entra-based SMTP credentials

## Installation

### Local Development

1. Clone the repository:

```bash
git clone https://github.com/seonghobae/azure-email-communication-service-sender.git
cd azure-email-communication-service-sender
```

1. Install dependencies:

```bash
pip install -r requirements.txt
```

1. Configure environment variables:

```bash
cp .env.example .env
# Edit .env with your configuration
```

1. Start Valkey (Redis-compatible):

```bash
docker run -d -p 6379:6379 valkey/valkey:7-alpine
```

1. Run the API service:

```bash
python -m app.main
```

1. Run the worker service (in another terminal):

```bash
python worker.py
```

### Docker Deployment

1. Configure environment variables:

```bash
cp .env.example .env
# Edit .env with your configuration
```

1. Start all services:

```bash
docker-compose up -d
```

1. View logs:

```bash
docker-compose logs -f
```

## Configuration

Configuration is done via environment variables (`.env` file):

| Variable | Description | Default |
|----------|-------------|---------|
| `SMTP_HOST` | SMTP server hostname | `smtp.azurecomm.net` |
| `SMTP_PORT` | SMTP server port | `587` |
| `SMTP_USERNAME` | Entra-based SMTP username | (required) |
| `SMTP_PASSWORD` | SMTP password | (required) |
| `ALLOWED_MAILFROM` | Comma-separated list of allowed sender addresses | (required) |
| `REDIS_URL` | Valkey/Redis connection URL | `redis://localhost:6379/0` |
| `DATABASE_URL` | Database connection URL | `postgresql+asyncpg://emailuser:emailpass@localhost:5432/emails` |
| `MAX_RETRIES` | Maximum retry attempts | `3` |
| `RETRY_DELAY_SECONDS` | Initial retry delay (exponential backoff) | `60` |
| `API_HOST` | API server host | `0.0.0.0` |
| `API_PORT` | API server port | `8000` |

## API Usage

### Send Email

**Endpoint**: `POST /api/v1/emails/`

**Request Body**:

```json
{
  "from": "sender@yourdomain.com",
  "envelope_from": "noreply@yourdomain.com",
  "to": ["recipient@example.com"],
  "cc": ["cc@example.com"],
  "bcc": ["bcc@example.com"],
  "subject": "Test Email",
  "body": "Email body content",
  "html": false,
  "caller_id": "service-a",
  "idempotency_key": "unique-key-123"
}
```

**Fields**:
- `from` (required): Header From address (RFC 5322.From)
- `envelope_from` (optional): Envelope Sender (RFC 5321.MailFrom) - defaults to `from` if not provided
- `to` (required): List of recipient addresses
- `cc` (optional): List of CC addresses
- `bcc` (optional): List of BCC addresses
- `subject` (required): Email subject
- `body` (required): Email body content
- `html` (optional): Whether body is HTML (default: false)
- `caller_id` (required): Caller identifier for multi-tenant isolation
- `idempotency_key` (optional): Unique key to prevent duplicate submissions

**Response** (201 Created):

```json
{
  "email_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "message": "Email queued for sending",
  "created_at": "2026-01-08T01:00:00"
}
```

**Example with curl**:

```bash
curl -X POST http://localhost:8000/api/v1/emails/ \
  -H "Content-Type: application/json" \
  -d '{
    "from": "sender@yourdomain.com",
    "to": ["recipient@example.com"],
    "subject": "Test Email",
    "body": "Hello, World!"
  }'
```

### Check Email Status

**Endpoint**: `GET /api/v1/emails/{email_id}`

**Response** (200 OK):

```json
{
  "email_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "sent",
  "from_address": "sender@yourdomain.com",
  "envelope_from": "sender@yourdomain.com",
  "to": ["recipient@example.com"],
  "subject": "Test Email",
  "created_at": "2026-01-08T01:00:00",
  "updated_at": "2026-01-08T01:01:00",
  "retry_count": 0,
  "error_message": null,
  "sent_at": "2026-01-08T01:01:00"
}
```

### Get Queue Statistics

**Endpoint**: `GET /api/v1/emails/`

**Response** (200 OK):

```json
{
  "queue_size": 10,
  "processing_size": 2,
  "dlq_size": 1
}
```

## Email Status Flow

1. **pending** → Email created and validated
2. **queued** → Email added to Valkey queue
3. **sending** → Worker is sending the email
4. **sent** → Email successfully sent
5. **failed** → Email failed to send (will retry)
6. **dlq** → Email moved to Dead Letter Queue after max retries

## SMTP Authentication vs. Sender Address

The service separates SMTP authentication from the sender address:

- **SMTP Username/Password**: Used for authentication with Azure Communication Services (Entra-based)
- **Envelope From (`envelope_from`)**: Used in the SMTP `MAIL FROM` command (RFC 5321)
- **Header From (`from`)**: Used in the email `From:` header (RFC 5322)

This separation allows you to:
1. Authenticate with one identity (Entra service principal)
2. Send emails from different verified sender addresses
3. Maintain proper SPF/DKIM/DMARC alignment

**Default Policy**: If `envelope_from` is not provided, it defaults to `from` (aligned).

## Security

- Only whitelisted addresses in `ALLOWED_MAILFROM` can be used as envelope sender
- STARTTLS is enforced for all SMTP connections
- Certificate validation is enabled
- Environment variables should be kept secure
- Use strong passwords for SMTP authentication

## Monitoring

The service provides structured logging for:
- Email submissions
- Queue operations
- SMTP send attempts
- Errors and retries
- Status changes

Logs can be collected and analyzed using standard log aggregation tools.

## API Documentation

Interactive API documentation is available at:
- Swagger UI: <http://localhost:8000/docs>
- ReDoc: <http://localhost:8000/redoc>

## License

See [LICENSE](LICENSE) file for details.
