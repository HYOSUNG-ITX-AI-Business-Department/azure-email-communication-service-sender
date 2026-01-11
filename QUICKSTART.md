# Quick Start Guide

## Prerequisites

- Python 3.11+
- PostgreSQL 12+ server
- Valkey server (Redis-compatible)
- Azure Communication Services Email resource with SMTP credentials

## Installation

1. Clone the repository:

```bash
git clone https://github.com/seonghobae/azure-email-communication-service-sender.git
cd azure-email-communication-service-sender
```

1. Install dependencies:

```bash
pip install -r requirements.txt
```

1. Configure environment:

```bash
cp .env.example .env
# Edit .env with your Azure ACS SMTP credentials and allowed sender addresses
```

## Running with Docker (Recommended)

```bash
# Configure .env file first
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

## Running Locally

Terminal 1 - Start Valkey:

```bash
docker run -d -p 6379:6379 valkey/valkey:7-alpine
```

Terminal 2 - Start API Server:

```bash
python -m app.main
```

Terminal 3 - Start Worker:

```bash
python worker.py
```

Access API documentation at: [http://localhost:8000/docs](http://localhost:8000/docs)

## Usage Examples

### Send Email (Default Envelope)

```bash
curl -X POST http://localhost:8000/api/v1/emails/ \
  -H "Content-Type: application/json" \
  -d '{
    "from": "sender@yourdomain.com",
    "to": ["recipient@example.com"],
    "subject": "Hello",
    "body": "This is a test email"
  }'
```

### Send Email (Separate Envelope)

```bash
curl -X POST http://localhost:8000/api/v1/emails/ \
  -H "Content-Type: application/json" \
  -d '{
    "from": "sender@yourdomain.com",
    "envelope_from": "noreply@yourdomain.com",
    "to": ["recipient@example.com"],
    "cc": ["cc@example.com"],
    "subject": "Important Update",
    "body": "<h1>HTML Email</h1>",
    "html": true,
    "idempotency_key": "unique-123"
  }'
```

### Check Email Status

```bash
curl http://localhost:8000/api/v1/emails/{email_id}
```

### Get Queue Statistics

```bash
curl http://localhost:8000/api/v1/emails/
```

## Environment Variables

### Required

- `SMTP_USERNAME` - Your Azure ACS SMTP username (Entra-based)
- `SMTP_PASSWORD` - Your Azure ACS SMTP password
- `ALLOWED_MAILFROM` - Comma-separated list of verified sender addresses

### Optional

- `SMTP_HOST` - SMTP server (default: smtp.azurecomm.net)
- `SMTP_PORT` - SMTP port (default: 587)
- `REDIS_URL` - Valkey/Redis connection URL (default: redis://localhost:6379/0)
- `DATABASE_URL` - Database URL (default: postgresql+asyncpg://emailuser:emailpass@localhost:5432/emails)
- `MAX_RETRIES` - Maximum retry attempts (default: 3)
- `RETRY_DELAY_SECONDS` - Initial retry delay (default: 60)
- `MAX_RETRY_DELAY_SECONDS` - Maximum retry delay, 0 disables cap (default: 0)
- `RETRY_DELAY_JITTER_SECONDS` - Retry delay jitter in seconds (default: 0)
- `API_HOST` - API host (default: 127.0.0.1)
- `API_PORT` - API port (default: 8000)
- For production behind a reverse proxy (Nginx/Traefik), set `API_HOST=0.0.0.0` explicitly.

## Testing

Run unit tests:

```bash
export SMTP_USERNAME="test@example.com"
export SMTP_PASSWORD="testpassword"
export ALLOWED_MAILFROM="sender@yourdomain.com"
python -m pytest tests/ -v
```

## Monitoring

### Health Check

```bash
curl http://localhost:8000/health
```

### Queue Statistics

```bash
curl http://localhost:8000/api/v1/emails/
```

Response:

```json
{
  "queue_size": 10,
  "processing_size": 2,
  "dlq_size": 1
}
```

## Troubleshooting

### Email stuck in "queued" status

- Check that the worker service is running
- Check worker logs for SMTP connection errors
- Verify SMTP credentials are correct

### "envelope_from not in allowed list" error

- Add the sender address to `ALLOWED_MAILFROM` environment variable
- Verify the address is verified in Azure Communication Services

### Connection refused to Valkey/Redis

- Ensure Valkey is running on the configured host/port
- Check `REDIS_URL` environment variable

## Architecture

```text
┌─────────┐    HTTP POST     ┌────────────┐    Valkey     ┌────────┐
│ Client  │ ──────────────>  │   API      │ ───────────>  │ Worker │
│         │                  │  Service   │               │        │
└─────────┘                  └────────────┘               └────────┘
                                   │                            │
                                   v                            v
                             ┌──────────┐               ┌──────────┐
                             │PostgreSQL│               │   SMTP   │
                             │ Database │               │  Azure   │
                             └──────────┘               └──────────┘
```

## API Response Examples

### Success Response

```json
{
  "email_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "message": "Email queued for sending",
  "created_at": "2026-01-08T01:00:00"
}
```

### Status Check Response

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

### Error Response

```json
{
  "detail": "envelope_from 'notallowed@domain.com' is not in allowed MailFrom list"
}
```

## Support

For issues and questions, please open an issue on GitHub.
