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
2. API validates tenant identity and persists an email record (`pending`).
3. API transitions record to `queued` and enqueues the email id.
4. Worker dequeues id, loads record, transitions to `sending`, attempts SMTP send.
5. Worker transitions to `sent` on success; on retryable errors, transitions to `failed`, increments retry, and requeues with delay; on permanent errors or retry exhaustion, transitions to `dlq` and moves to DLQ.

## REST API
### `POST /api/v1/emails/`
- Purpose: Submit an email request.
- Authentication/Authorization:
  - Requires `X-Caller-Id` header (trusted upstream identity).
  - Requires request `caller_id` to match `X-Caller-Id`.
- Idempotency:
  - Per-caller: composite uniqueness on `caller_id + idempotency_key`.
  - Avoids duplicate enqueue on idempotency replay by queueing only when status is `pending`.

### `GET /api/v1/emails/{email_id}`
- Purpose: Fetch status/details.
- Authorization: Caller-scoped (`email.caller_id` must match `X-Caller-Id`).
- Defensive parsing: stored `to_addresses` is normalized to `list[str]`, and unknown DB `status` falls back safely.

### `GET /api/v1/emails/` (Queue stats)
- Purpose: Queue sizes for monitoring.
- Authorization: Requires `X-Caller-Id` and `QUEUE_STATS_ALLOWED_CALLERS` allowlist.

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
- Scaling and concurrency:
  - Current behavior:
    - Queue consumption uses atomic `BLMOVE(queue → processing)`, so two workers should not dequeue the same list item at the same time.
    - The system is still effectively “at-least-once” overall if the same email id is enqueued multiple times (e.g., operator requeue or bugs), or if a worker crashes mid-flight.
  - Known gaps / failure modes:
    - No processing timeout/reaper: if a worker dies, ids can remain stuck in `email:processing` without automatic recovery.
    - No per-email locking: if an email id appears twice, multiple workers may send it unless the handler is made idempotent.
    - DB + queue are not transactional: status transitions and queue mutations can diverge during partial failures.
  - Recommended mitigations:
    - Add a processing reaper/visibility timeout mechanism, or move to a queue primitive with visibility timeouts (e.g., Redis Streams consumer groups) if needed.
    - Add a per-email distributed lock and/or a stronger idempotent send guard in the worker (beyond “skip if sent”).
    - Consider an outbox/sweeper pattern to reconcile `queued` records and queue state.
- Operations (recommended runbook topics):
  - Monitoring/alerting: track queue sizes, send success/failure rate, retry/DLQ volume, dependency health, and latency; aggregate logs centrally.
  - Backup/DR: define DB backup + restore testing; decide Redis persistence strategy (and what data loss is acceptable).
  - API deployment: run multiple API instances behind a load balancer; ensure all instances share the same DB and Redis.
  - Performance: tune DB indexes, connection pooling, worker concurrency, and retry/backoff settings per environment.
