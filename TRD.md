# Azure Email Communication Service Sender — TRD

> **Production readiness: NOT COMPLETE**
>
> Do not deploy to production until the following are implemented and validated:
> - Processing visibility timeout + reaper (recovery of stuck `email:processing` items)
> - Per-email distributed lock and/or stronger idempotent send guard
> - DB↔queue reconciliation (outbox/sweeper) to prevent state divergence

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
  - Known limitation: near-simultaneous requests may both enqueue before the record transitions out of `pending`; see Production Considerations for mitigations.

### `GET /api/v1/emails/{email_id}`

- Purpose: Fetch status/details.
- Authorization: Caller-scoped (`email.caller_id` must match `X-Caller-Id`).
- Defensive parsing:
  - `to_addresses` is normalized to `list[str]` (malformed values fall back to `[]` and are logged).
  - Unknown DB `status` values are logged at WARN and mapped to `failed`.
  - Example: `to_addresses='[\"a@example.com\"]'` → `["a@example.com"]`; `status="unexpected"` → `failed`.

### `GET /api/v1/emails/` (Queue stats)

- Purpose: Queue sizes for monitoring.
- Authorization: Requires `X-Caller-Id` and `QUEUE_STATS_ALLOWED_CALLERS` allowlist.

### Health/Readiness

- `/health`: dependency health (Redis + DB); returns 503 when unhealthy.
- `/ready` and `/readyz`: readiness check with dependency status; returns 503 when not ready.
- `/healthz`: liveness-only; always returns 200.

## Observability

### Health and Readiness Checks

- Current implementation:
  - Redis: `PING` via `queue_service.redis_client.ping()` when connected.
  - DB: `SELECT 1` via `AsyncSessionLocal`.
  - `/health` and `/ready` return 503 if any dependency check fails and include a `checks` map.
  - Partial degradation is not supported (any failing dependency ⇒ not ready/unhealthy).
- Recommended production settings:
  - Add short, per-dependency timeouts (e.g., ~1s) and avoid retries to keep signals crisp.

### Metrics

- Required metrics (recommended):
  - API: request rate, status codes, latency (p50/p90/p99).
  - Worker: send attempts, success/failure rates, retry counts, DLQ moves, SMTP response code counts.
  - Queue: sizes of `email:queue`, `email:processing`, `email:delayed`, `email:dlq`.
- Collection:
  - Recommended: Prometheus/OpenTelemetry exporter (not implemented in this repo yet).
  - Interim: derive from DB statuses + logs; use Redis queries (or the queue stats endpoint) for queue sizes.
- Cadence: collect at 15–60s intervals and roll up to 1m/5m windows for SLO tracking.

### Logging

- Recommended production requirements:
  - Prefer structured JSON logs; include `email_id`, `caller_id`, `status`, `retry_count`, and error metadata.
  - Use levels consistently: INFO for normal transitions, WARN for recoverable anomalies, ERROR for failures.
  - Mask PII: do not log message bodies/attachments; redact or hash email addresses when possible.

### Tracing

- Optional: OpenTelemetry tracing with propagated context (e.g., `traceparent`).
- Suggested spans: API request, DB operations, enqueue/dequeue, SMTP send.
- Sampling: start low (e.g., 1–5%) in production and increase during incident investigations.

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
- SMTP:
  - `SMTP_HOST` (optional, string, default: `smtp.azurecomm.net`)
  - `SMTP_PORT` (optional, int, default: `587`)
  - `SMTP_USERNAME` (required, string)
  - `SMTP_PASSWORD` (required, string; secret)
  - Secrets (examples; do not commit to git):
    - Strategy: dev uses local `.env`; staging/prod should inject secrets from a remote secret store and CI/CD.
    - Azure Key Vault: store `SMTP_USERNAME`/`SMTP_PASSWORD` as secrets and grant the app a Managed Identity with secret `get` access.
    - GitHub Actions: store `SMTP_*` as repository/org secrets and pass them as environment variables, e.g.

      ```yaml
      env:
        SMTP_HOST: ${{ secrets.SMTP_HOST }}
        SMTP_PORT: ${{ secrets.SMTP_PORT }}
        SMTP_USERNAME: ${{ secrets.SMTP_USERNAME }}
        SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
      ```

    - Rotation checklist: rotate credentials, update vault/CI secrets, deploy, verify sends, keep a rollback plan.
  - TLS: SMTP sending uses STARTTLS with certificate validation enabled; ensure the runtime has an appropriate CA bundle.
- Security:
  - `ALLOWED_MAILFROM` (required, comma-separated string; must contain at least one valid address)
  - `ALLOWED_HEADERS` (optional, comma-separated string; required when sending custom headers)
  - `QUEUE_STATS_ALLOWED_CALLERS` (optional, comma-separated string; required to enable queue stats endpoint)
- Infra:
  - `REDIS_URL` (optional, string URL, default: `redis://localhost:6379/0`)
  - `DATABASE_URL` (optional, string URL, default: `postgresql+asyncpg://emailuser@localhost:5432/emails`)
  - Secrets: prefer secret manager injection for any credentials embedded in connection URLs.
  - TLS/SSL:
    - Redis: use the `rediss://` scheme in `REDIS_URL` (no extra flags), e.g. `REDIS_URL=rediss://:password@host:6379/0`.
    - DB: configure TLS via `DATABASE_URL` options (e.g., Postgres `sslmode=require`) and the driver's standard TLS parameters.
- Retry:
  - `MAX_RETRIES` (optional, int, default: `3`)
  - `RETRY_DELAY_SECONDS` (optional, int, default: `60`)
  - `MAX_RETRY_DELAY_SECONDS` (optional, int, default: `0` meaning no cap)
  - `RETRY_DELAY_JITTER_SECONDS` (optional, int, default: `0` meaning no jitter)
- API:
  - `API_HOST` (optional, string, default: `127.0.0.1`; set `0.0.0.0` in production)
  - `API_PORT` (optional, int, default: `8000`)
  - `DEBUG` (optional, bool, default: `false`; also controls uvicorn reload and dev-only DB auto-creation)
- Operations (suggested future options):
  - Worker tuning: `WORKER_COUNT`, `BATCH_SIZE`
  - Logging: `LOG_LEVEL`, `LOG_FORMAT`, `LOG_OUTPUT`
  - Keep a separate security/ops guide for production deployments.

## Production Considerations

- Database schema management:
  - `init_db()` uses SQLAlchemy `create_all` and is only invoked when `DEBUG=true`.
  - Production must keep `DEBUG=false` and run migrations (e.g., Alembic) as part of deploy.
  - Recommended: add a CI/CD gate that fails production deployments when `DEBUG=true`.
  - Risk: accidentally deploying with `DEBUG=true` can cause unintended schema drift (auto-creating tables) and make migrations/rollbacks unsafe.
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
  - Monitoring/alerting (examples; tune per environment):
    - P1: DLQ size > 100, dependency health check failures > 2 minutes, database/Redis connectivity failures.
    - P2: queue size > 1000 for > 5 minutes, send failure rate > 5% (5m rolling), p95 end-to-end latency > 30s.
    - Track retry and DLQ write volume spikes, SMTP response codes, and worker crash/restart rate.
    - Aggregate logs centrally (e.g., Azure Monitor, ELK).
  - Backup/DR:
    - DB: daily full backup + point-in-time recovery; rehearse restores.
    - Redis persistence: prefer AOF for durability; document acceptable data-loss window (e.g., < 5 minutes) and a requeue/recovery procedure.
  - Performance baselines and sizing:
    - Measure throughput per worker (emails/sec) under realistic SMTP quotas and p95 latency.
    - Example: if p95 SMTP send time is ~500ms, one worker ≈ 2 emails/sec; for 20 emails/sec, start with ~12 workers (2× headroom).
    - Tune DB indexes, connection pooling, worker concurrency, and retry/backoff settings per environment.
  - API deployment: run multiple API instances behind a load balancer; ensure all instances share the same DB and Redis.

## Operational Runbook (Outline)

> Tracked in Issue #8 and treated as a production blocker until completed.

### Common Failure Scenarios

- Queue backpressure (queue size grows continuously):
  - Signals: rising `email:queue` length, p95 latency increasing, rising retries.
  - Actions: scale workers, investigate SMTP throttling/errors, verify DB/Redis health, consider temporarily reducing intake at the caller/ingress.
- Stuck processing (items accumulate in `email:processing`):
  - Signals: `email:processing` grows while `email:queue` stays flat; worker restarts/crashes.
  - Actions: inspect worker logs and dependency health; consider manual requeue after confirming the worker is not still processing those ids.
- SMTP/ACS auth failures:
  - Signals: repeated 535/5xx SMTP auth errors; sudden spike in failed sends.
  - Actions: validate `SMTP_USERNAME`/`SMTP_PASSWORD` secret injection, rotate credentials, and verify TLS/cert trust on the runtime.
- DLQ spikes:
  - Signals: `email:dlq` grows rapidly; permanent SMTP errors (5xx) or retry exhaustion.
  - Actions: sample DLQ entries for root causes, fix configuration/payload issues, and requeue only after mitigating the underlying cause.
- Dependency outage (Redis/DB):
  - Signals: `/health` returns 503, connection errors in logs.
  - Actions: restore dependency availability; scale workers down if they are crash-looping; verify recovery by watching queue movement and error rates.

### Safety Mode / Circuit Breakers (Operational)

- Stop/scale down workers when upstream SMTP or dependencies are unstable to avoid runaway retries/DLQ growth.
- Apply ingress controls (rate limits / temporary blocks) at the caller or gateway when queue backpressure exceeds agreed thresholds.

### Backup & Recovery

- Database:
  - Backups: define cadence and retention; validate restores regularly.
  - Recovery: after restore, reconcile queued/failed records vs. Redis queue state (see Scaling and concurrency gaps).
- Redis:
  - Persistence: prefer AOF for durability; decide acceptable data-loss window and document recovery steps.

### Deploy & Rollback

- Before deploy: ensure `DEBUG=false`, run migrations (Alembic), and validate `/health` and `/ready` in the target environment.
- Deploy order: API first (stateless), then workers; roll out gradually while monitoring error rates and queue metrics.
- Rollback: revert application versions; if migrations were applied, follow the DB rollback plan and verify schema compatibility.

### Metrics, Dashboards, and Alerts

- Collection (recommended 1-minute granularity):
  - Delivery/retry/DLQ rates: derive from DB statuses and worker logs.
  - Queue metrics: track `email:queue`, `email:processing`, `email:dlq`, and `email:delayed` sizes.
- Dashboards: maintain both SLO views (PRD Success Metrics) and operational views (queue sizes, latency, error codes).
- Alerts: trigger on sustained SLO breaches and the Monitoring/alerting thresholds listed above; define P1/P2 escalation paths.

### Useful Commands (Examples)

- Check health/readiness: `curl -fsS http://<host>:8000/health` and `curl -fsS http://<host>:8000/ready`
- Check queue stats endpoint (requires allowlist): `curl -H 'X-Caller-Id: <ops-id>' http://<host>:8000/api/v1/emails/`
- Inspect Redis queues: `redis-cli LLEN email:queue`, `redis-cli LLEN email:processing`, `redis-cli LLEN email:dlq`, `redis-cli ZCARD email:delayed`
