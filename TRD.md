# Azure Email Communication Service Sender — TRD

## Architecture
### Components
- **Sender API (FastAPI)**: Validates requests, persists records, queues email ids.
- **Worker (async loop)**: Dequeues ids, loads records from DB, sends via SMTP, updates status, requeues or DLQs as needed.
- **Database (SQLAlchemy)**: Stores `EmailRecord` with status, retry count, timestamps, and audit log.
- **Queue (Redis/Valkey)**: Lists/sets used to queue, track processing, and hold DLQ items; supports delayed retries.
- **SMTP Relay (ACS)**: Delivery backend via STARTTLS-authenticated SMTP.

### High-level Flow
1. Client calls `POST /api/v1/emails/` with `X-Caller-Id` and payload.
2. API validates tenant identity and persists an email record (`PENDING`).
3. API transitions record to `QUEUED` and enqueues the email id.
4. Worker dequeues id, loads record, transitions to `SENDING`, attempts SMTP send.
5. Worker transitions to `SENT` on success; on retryable errors, transitions to `FAILED`, increments retry, and requeues with delay; on permanent errors or retry exhaustion, transitions to `DLQ` and moves to DLQ.

## REST API
### `POST /api/v1/emails/`
- Purpose: Submit an email request.
- AuthN/AuthZ:
  - Requires `X-Caller-Id` header (trusted upstream identity).
  - Requires request `caller_id` to match `X-Caller-Id`.
- Idempotency:
  - Per-caller: composite uniqueness on `caller_id + idempotency_key`.
  - Avoids duplicate enqueue on idempotency replay by queueing only when status is `pending`.

### `GET /api/v1/emails/{email_id}`
- Purpose: Fetch status/details.
- AuthZ: Caller-scoped (`email.caller_id` must match `X-Caller-Id`).
- Defensive parsing: stored `to_addresses` is normalized to `list[str]`, and unknown DB `status` falls back safely.

### `GET /api/v1/emails/` (Queue stats)
- Purpose: Queue sizes for monitoring.
- AuthZ: Requires `X-Caller-Id` and `QUEUE_STATS_ALLOWED_CALLERS` allowlist.

### Health/Readiness
- `/health`: dependency health (Redis + DB); returns 503 when unhealthy.
- `/ready` and `/readyz`: readiness check with dependency status; returns 503 when not ready.
- `/healthz`: liveness-only; always returns 200.

## Data Model
### `EmailRecord` (conceptual)
- Identifiers:
  - `id` (uuid string)
  - `caller_id` (tenant identity)
  - `idempotency_key` (optional; unique per caller)
- Addresses:
  - `from_address`
  - `envelope_from` (defaults to `from_address` when omitted)
  - `to_addresses`, optional `cc_addresses`, `bcc_addresses`
  - optional `reply_to`
- Content:
  - `subject`, `body`, `is_html`
  - optional `headers` (allowlisted)
  - optional `attachments`, `tags`
- Delivery state:
  - `status` (pending/queued/sending/sent/failed/dlq)
  - `retry_count`, `error_message`
  - timestamps: `created_at`, `updated_at`, optional `sent_at`
  - `audit_log` (JSON list of status transitions)

## Queue Design (Redis/Valkey)
### Keys
- `email:queue`: pending work (list)
- `email:processing`: in-flight work (list; populated via `BLMOVE`)
- `email:delayed`: delayed retry schedule (sorted set)
- `email:dlq`: dead letter queue (list of JSON items)

### Startup Scripts
`QueueService.connect()` registers Lua scripts used by:
- Move to DLQ (atomic removal from processing + push to DLQ)
- Requeue (atomic removal from processing + push to queue)
- Requeue delayed (atomic removal from processing + add to delayed ZSET)
- Move ready delayed items (ZSET → queue)

## Worker Behavior
- Dequeue uses `BLMOVE(queue → processing)` for atomic handoff.
- Before sending:
  - If record is missing, worker completes the queue item.
  - If status is `sent`, worker skips and completes.
- Retry:
  - `calculate_backoff_delay(retry_count, base_delay)` uses exponential backoff; optional max cap and jitter.
  - Worker schedules delayed requeue via `requeue_delayed(email_id, delay_seconds)`.
- DLQ:
  - After `MAX_RETRIES`, move to DLQ and persist `DLQ` status with error reason.

## Configuration
Key environment variables:
- SMTP: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`
- Security: `ALLOWED_MAILFROM`, `ALLOWED_HEADERS`, `QUEUE_STATS_ALLOWED_CALLERS`
- Infra: `REDIS_URL`, `DATABASE_URL`
- Retry: `MAX_RETRIES`, `RETRY_DELAY_SECONDS`, `MAX_RETRY_DELAY_SECONDS`, `RETRY_DELAY_JITTER_SECONDS`
- API: `API_HOST`, `API_PORT`, `DEBUG` (also controls uvicorn reload and dev-only DB auto-creation)

## Production Considerations
- Database schema management:
  - `init_db()` uses SQLAlchemy `create_all` and is only invoked when `DEBUG=true`.
  - Production should run migrations (e.g., Alembic) as part of deploy.
- Scaling:
  - Multiple workers can be run, but queue duplication and record state transitions must be considered carefully (distributed locking/outbox patterns may be added in future work).
