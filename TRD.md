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
- Validation/security:
  - `envelope_from` must be allowlisted via `ALLOWED_MAILFROM`.
  - Custom headers are allowlisted via `ALLOWED_HEADERS` (case-insensitive match) and CR/LF characters are rejected in header names/values to prevent injection.

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
  - Mask PII: never log message bodies, attachments, or email addresses; log only internal identifiers (`email_id`, `caller_id`).
  - Never log custom headers or header values; they can contain auth tokens or other sensitive data.

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

### Deliverability Considerations

- Deliverability depends on domain alignment between `from_address` (RFC 5322.From) and `envelope_from` (RFC 5321.MailFrom).
- Recommended:
  - Use verified/custom sending domains in your email provider (ACS) and keep `envelope_from` within that domain (enforced by `ALLOWED_MAILFROM`).
  - Configure and validate SPF for the `envelope_from` domain (RFC 7208).
  - Configure DKIM signing for the From domain (RFC 6376) and align with your DMARC policy (RFC 7489).
  - Choose DMARC alignment (relaxed vs strict) based on organizational policy and monitor DMARC reports.

## Queue Design (Redis/Valkey)

### Keys

- `email:queue`: pending work (list)
- `email:processing`: in-flight work (list; populated via `BLMOVE`)
- `email:delayed`: delayed retry schedule (sorted set)
- `email:dlq`: dead letter queue (list of JSON objects with `email_id` and `error`, e.g. `{"email_id":"...","error":"..."}`)

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
  - `calculate_backoff_delay(retry_count, base_delay)` uses exponential backoff:
    - Formula: `base_delay * (2 ** max(retry_count - 1, 0))` where `base_delay = RETRY_DELAY_SECONDS`.
    - Cap: if `MAX_RETRY_DELAY_SECONDS > 0`, the computed delay is capped to that value.
    - Jitter: if `RETRY_DELAY_JITTER_SECONDS > 0`, add a random `0..jitter` seconds.
    - Example (defaults): `retry_count=1→60s`, `2→120s`, `3→240s` (+ optional jitter).
  - Worker schedules delayed requeue via `requeue_delayed(email_id, delay_seconds)`.
- DLQ:
  - After `MAX_RETRIES`, move to DLQ and persist `DLQ` status with error reason.

## Configuration

Key environment variables:
- SMTP:
  - `SMTP_HOST` (optional, string, default: `smtp.azurecomm.net`)
  - `SMTP_PORT` (optional, int, default: `587`; port `25` may also be supported depending on provider/network policy)
  - `SMTP_USERNAME` (required, string)
    - ACS note: treat as an opaque, ACS-provisioned SMTP username (copy from the Azure Portal “SMTP Usernames” blade). See [Microsoft Learn: SMTP authentication](https://learn.microsoft.com/azure/communication-services/quickstarts/email/send-email-smtp/smtp-authentication).
  - `SMTP_PASSWORD` (required, string; secret)
  - Auth: this implementation uses SMTP AUTH (username + password). Token-based SMTP auth (e.g., XOAUTH2) is not supported by the SMTP client in this repo today.
  - Azure prerequisites: provisioning SMTP usernames/secrets requires Azure RBAC on the Communication Services resource; use a built-in Contributor/Owner role or a custom role with at least `Microsoft.Communication/CommunicationServices/read`, `Microsoft.Communication/CommunicationServices/write`, and the SMTP username operations you need (domain/sender management may require `Microsoft.Communication/EmailServices/*`).
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
  - TLS: SMTP sending uses STARTTLS with certificate validation enabled; ensure the runtime has an appropriate CA bundle and supports TLS 1.2+ (Azure requires TLS 1.2 or later).
- Security:
  - `ALLOWED_MAILFROM` (required, comma-separated string)
    - Format: `local@domain` (wildcards are not supported).
    - Normalization: domain is lowercased; local-part is preserved.
    - Example: `noreply@example.com,support@example.com`
  - `ALLOWED_HEADERS` (optional, comma-separated string; required when sending custom headers)
    - Matching: case-insensitive against request header names.
    - Example: `X-Priority,X-Custom-Tag`
    - Validation: request header names/values reject CR/LF (`\r`/`\n`) to prevent injection.
  - `QUEUE_STATS_ALLOWED_CALLERS` (optional, comma-separated string; required to enable queue stats endpoint)
    - Format: caller IDs matched against the `X-Caller-Id` header.
    - Example: `ops-service,admin-dashboard`
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
  - Recommended: add a CI/CD gate that fails production deployments when `DEBUG=true` (example GitHub Actions):

    ```yaml
    - name: Guard unsafe configuration (production)
      run: |
        if [ "${DEBUG:-false}" = "true" ]; then
          echo "ERROR: DEBUG=true is not allowed in production"
          exit 1
        fi

        # Fail if real .env files are committed (tune for your repo).
        if git ls-files | grep -E '^\\.env(\\.|$)' | grep -vq '^\\.env\\.example$'; then
          echo "ERROR: real .env files must not be committed"
          git ls-files | grep -E '^\\.env(\\.|$)' | grep -v '^\\.env\\.example$'
          exit 1
        fi

        # Ensure required secrets are injected (typically via CI secret store).
        : "${SMTP_PASSWORD:?SMTP_PASSWORD must be set}"
        : "${DATABASE_URL:?DATABASE_URL must be set}"
    ```

    Adapt this check to validate the final deploy-time environment/config in your CI system.
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
    - Implementation sketches (future work; copy-paste starting points):
      - Processing reaper / visibility timeout (List + ZSET):
        - Keys: `email:queue` (list), `email:processing` (list), `email:processing:vis` (zset with score=expiry epoch seconds).
        - Worker flow: after dequeue, `ZADD email:processing:vis (now+timeout) <email_id>`; optionally refresh/heartbeat during long sends.
        - Reaper flow: run every 30–60s and move expired ids back to `email:queue` using a Lua script.

        ```lua
        -- KEYS[1]=processing_list, KEYS[2]=queue_list, KEYS[3]=processing_vis_zset
        -- ARGV[1]=now_epoch_seconds, ARGV[2]=max_batch
        local processing = KEYS[1]
        local queue = KEYS[2]
        local vis = KEYS[3]
        local now = tonumber(ARGV[1])
        local max_batch = tonumber(ARGV[2])

        local ids = redis.call('ZRANGEBYSCORE', vis, '-inf', now, 'LIMIT', 0, max_batch)
        local moved = 0
        for _, id in ipairs(ids) do
          redis.call('ZREM', vis, id)
          local removed = redis.call('LREM', processing, 1, id)
          if removed > 0 then
            redis.call('LPUSH', queue, id)
            moved = moved + 1
          end
        end
        return moved
        ```

      - Per-email lock (Redis `SET NX EX`) around the send path:

        ```python
        lock_key = f\"email:lock:{email_id}\"
        token = uuid4().hex
        if not redis.set(lock_key, token, nx=True, ex=60):
            return \"locked\"  # skip/requeue/backoff

        try:
            send_email(email_id)
        finally:
            redis.eval(
                \"if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0\",
                1,
                lock_key,
                token,
            )
        ```

      - Outbox/sweeper reconciliation job:
        - Run every 1–5 minutes, batch (e.g., 100 ids).
        - Query: DB rows with `status='queued'` (or `pending`) older than a small grace window and re-enqueue missing work.
        - Implementation option: maintain an index key (e.g., `email:queued:set`) so the sweeper can `SISMEMBER` cheaply, or rely on the per-email lock/idempotent worker to tolerate duplicates.
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

> Tracked in #8 and treated as a production blocker until completed.
>
> Completion checklist (fill in owners/links for your org):
> - [ ] Backup policy (DB/Redis) defined with RPO/RTO targets
> - [ ] Restore procedure documented and tested (at least monthly)
> - [ ] Deploy/rollback plan documented (including DB migration rollback expectations)
> - [ ] Escalation path defined for P1/P2 incidents (contacts + SLAs)
> - [ ] Game day executed and runbook updated

### Common Failure Scenarios

- Queue backpressure (queue size grows continuously):
  - Signals: rising `email:queue` length, p95 latency increasing, rising retries.
  - Actions: scale workers, investigate SMTP throttling/errors, verify DB/Redis health, consider temporarily reducing intake at the caller/ingress.
- Stuck processing (items accumulate in `email:processing`):
  - Signals: `email:processing` grows while `email:queue` stays flat; worker restarts/crashes.
  - Actions: inspect worker logs and dependency health; consider manual requeue after confirming the worker is not still processing those ids.
- SMTP/ACS auth failures:
  - Signals: repeated 535/5xx SMTP auth errors; sudden spike in failed sends.
  - Actions:
    - Validate `SMTP_USERNAME`/`SMTP_PASSWORD` secret injection and rotate credentials if needed.
    - Verify network connectivity to `smtp.azurecomm.net:587` (e.g., `nc -zv smtp.azurecomm.net 587` or `telnet smtp.azurecomm.net 587`), DNS resolution, and outbound firewall/proxy/NAT rules.
    - Review any ACS/infra IP allowlists and firewall rules if configured.
    - Verify TLS/cert trust on the runtime.
    - Check [Azure service health](https://status.azure.com/status) and open an Azure support ticket via Azure Portal if the issue is widespread.
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

- Database (recommended starting policy; tune per org):
  - Backups: daily full backup (e.g., 02:00 UTC) with 7-day retention.
  - PITR: enable point-in-time recovery; keep a 72-hour window (≤ 1-hour granularity).
  - Restore procedure (template):
    - Stop/scale down workers to prevent concurrent sends during recovery.
    - Restore DB (backup or PITR), then verify basic connectivity via `/health`.
    - Reconcile queued work vs. Redis (outbox/sweeper or manual requeue) before resuming processing.
    - Restart workers and monitor DLQ size and failure rates.
  - Validation: run a monthly restore test in staging.
- Redis (recommended starting policy; tune per org):
  - Persistence: AOF with `appendfsync everysec`.
  - Targets: RPO < 5 minutes, RTO < 15 minutes.
  - Recovery: restart Redis and repopulate missing work from DB (outbox/sweeper) or via the recovery commands below.

### Deploy & Rollback

- Before deploy: ensure `DEBUG=false`, run migrations (Alembic), and validate `/health` and `/ready` in the target environment.
- Deploy order: API first (stateless), then workers; roll out gradually while monitoring error rates and queue metrics.
- Rollback (template):
  - Revert application versions first (API + worker images).
  - If DB migrations were applied: run a tested Alembic downgrade (preferred) or restore DB from backup/PITR.
  - Verify `/health` and queue processing, then continue to monitor DLQ/failure rates.
  - Requirement: each migration must have a tested downgrade path or an explicit restore procedure.

### Metrics, Dashboards, and Alerts

- Collection (recommended 1-minute granularity):
  - Delivery/retry/DLQ rates: derive from DB statuses and worker logs.
  - Queue metrics: track `email:queue`, `email:processing`, `email:dlq`, and `email:delayed` sizes.
- Dashboards: maintain both SLO views (PRD Success Metrics) and operational views (queue sizes, latency, error codes).
- Alerts: trigger on sustained SLO breaches and the Monitoring/alerting thresholds listed above; define P1/P2 escalation paths.

### Escalation (Template)

- P1 (Critical): page on-call immediately (PagerDuty/phone); escalate to the platform lead after 15 minutes if not mitigated.
- P2 (Major): notify the platform channel; escalate to on-call after 60 minutes if not mitigated.
- Vendor escalation: for ACS/SMTP-wide issues, open an Azure support ticket via Azure Portal and check [Azure service health](https://status.azure.com/status).

### Useful Commands (Categorized)

- Monitoring (read-only; safe):
  - Health/readiness: `curl -fsS http://<host>:8000/health` and `curl -fsS http://<host>:8000/ready` (Use: verify dependency status before/after deploys.)
  - Queue stats endpoint (requires allowlist): `curl -H 'X-Caller-Id: <ops-id>' http://<host>:8000/api/v1/emails/` (Use: quick queue sizing without Redis access.)
  - Redis queue sizes: `redis-cli LLEN email:queue`, `redis-cli LLEN email:processing`, `redis-cli LLEN email:dlq`, `redis-cli ZCARD email:delayed` (Use: monitor backpressure and DLQ growth.)
  - Inspect DLQ entries (JSON): `redis-cli --raw LRANGE email:dlq 0 10` (Use: sample root causes from `error` values.)
  - Worker status/logs (docker-compose): `docker compose ps worker` and `docker compose logs -f worker` (Use: confirm crash loops and correlate auth/network failures.)
  - Find emails stuck in `sending` (Postgres example; investigation):

    ```sql
    SELECT id, status, updated_at
    FROM emails
    WHERE status = 'sending' AND updated_at < NOW() - INTERVAL '10 minutes'
    ORDER BY updated_at ASC
    LIMIT 50;
    ```

- Recovery (write operations; use with caution):
  - Safety checklist:
    - Confirm the id is not actively being processed (check worker logs/health) before requeueing from `email:processing` to avoid duplicate sends.
    - Fix the root cause (SMTP auth, configuration, payload validation) before requeueing DLQ items, otherwise they will likely fail again.
    - Monitor DLQ size and failure rates after requeue to catch recurrence quickly.
  - Manual requeue from processing (Use: recover ids stuck in `email:processing` after verifying they are not active):
    - Move one item (Redis 6.2+): `redis-cli LMOVE email:processing email:queue RIGHT LEFT`
    - Move a specific id: `redis-cli LREM email:processing 1 <email_id> && redis-cli LPUSH email:queue <email_id>`
  - Requeue one DLQ item (Use: after fixing root cause; requires `jq`):
    - `redis-cli --raw LPOP email:dlq | jq -r '.email_id' | xargs -I {} redis-cli LPUSH email:queue {}`

- Runbook hygiene: perform periodic game days (e.g., quarterly) to validate procedures and update thresholds.
