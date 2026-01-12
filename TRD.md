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

- OpenAPI:
  - `GET /openapi.json` (machine-readable schema)
  - `GET /docs` (interactive Swagger UI)

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
- Request schema (simplified JSON Schema):

  ```json
  {
    "type": "object",
    "required": ["caller_id", "from", "to", "subject", "body"],
    "properties": {
      "caller_id": {"type": "string", "maxLength": 256},
      "idempotency_key": {"type": ["string", "null"], "maxLength": 256},
      "from": {"type": "string", "format": "email"},
      "envelope_from": {"type": ["string", "null"], "format": "email"},
      "to": {"type": "array", "minItems": 1, "items": {"type": "string", "format": "email"}},
      "cc": {"type": ["array", "null"], "items": {"type": "string", "format": "email"}},
      "bcc": {"type": ["array", "null"], "items": {"type": "string", "format": "email"}},
      "subject": {"type": "string", "maxLength": 1000},
      "body": {"type": "string", "maxLength": 1000000},
      "html": {"type": "boolean"},
      "reply_to": {"type": ["string", "null"], "format": "email"},
      "headers": {
        "type": ["object", "null"],
        "maxProperties": 50,
        "propertyNames": {"type": "string", "maxLength": 128},
        "additionalProperties": {"type": "string", "maxLength": 2048}
      },
      "tags": {"type": ["array", "null"], "maxItems": 50, "items": {"type": "string", "maxLength": 128}},
      "attachments": {
        "type": ["array", "null"],
        "maxItems": 10,
        "items": {
          "type": "object",
          "required": ["filename", "content_base64"],
          "properties": {
            "filename": {"type": "string", "maxLength": 255},
            "content_type": {"type": "string", "maxLength": 255},
            "content_base64": {"type": "string", "maxLength": 14000000}
          }
        }
      },
      "smtp_auth_profile_id": {"type": ["string", "null"], "maxLength": 256}
    }
  }
  ```

- Response schema (202 Accepted):

  ```json
  {
    "type": "object",
    "required": ["email_id", "status", "message", "created_at"],
    "properties": {
      "email_id": {"type": "string"},
      "status": {"type": "string", "enum": ["pending", "queued", "sending", "sent", "failed", "dlq"]},
      "message": {"type": "string"},
      "created_at": {"type": "string", "format": "date-time"}
    }
  }
  ```

- Example request:

  ```json
  {
    "caller_id": "service-a",
    "idempotency_key": "order-123-receipt",
    "from": "noreply@example.com",
    "envelope_from": "bounce@example.com",
    "to": ["user@example.com"],
    "subject": "Order Receipt",
    "body": "Thank you for your order.",
    "html": false
  }
  ```

- Example response:

  ```json
  {
    "email_id": "uuid-here",
    "status": "queued",
    "message": "Email queued for sending",
    "created_at": "2026-01-11T16:00:00+00:00"
  }
  ```

- Error responses:
  - Format: `{"detail": "..."}`
  - Common status codes:
    - 400: invalid request fields or configuration (e.g., `ALLOWED_MAILFROM`)
    - 403: `caller_id` does not match `X-Caller-Id`
    - 409: idempotency key reused with a different payload
    - 422: request model validation error (FastAPI/Pydantic)
    - 500: internal error (e.g., enqueue failure)

### `GET /api/v1/emails/{email_id}`

- Purpose: Fetch status/details.
- Authorization: Caller-scoped (`email.caller_id` must match `X-Caller-Id`).
- Defensive parsing:
  - `to_addresses` is normalized to `list[str]` (malformed values fall back to `[]` and are logged).
  - Unknown DB `status` values are logged at WARN and mapped to `failed`.
  - Example: `to_addresses='[\"a@example.com\"]'` → `["a@example.com"]`; `status="unexpected"` → `failed`.
- Response schema (200 OK):

  ```json
  {
    "type": "object",
    "required": ["email_id", "status", "from_address", "envelope_from", "to", "subject", "created_at", "updated_at", "retry_count", "caller_id"],
    "properties": {
      "email_id": {"type": "string"},
      "status": {"type": "string", "enum": ["pending", "queued", "sending", "sent", "failed", "dlq"]},
      "from_address": {"type": "string"},
      "envelope_from": {"type": "string"},
      "to": {"type": "array", "items": {"type": "string"}},
      "subject": {"type": "string"},
      "created_at": {"type": "string", "format": "date-time"},
      "updated_at": {"type": "string", "format": "date-time"},
      "retry_count": {"type": "integer"},
      "error_message": {"type": ["string", "null"]},
      "sent_at": {"type": ["string", "null"], "format": "date-time"},
      "caller_id": {"type": "string"},
      "smtp_auth_profile_id": {"type": ["string", "null"]}
    }
  }
  ```

- Example response:

  ```json
  {
    "email_id": "uuid-here",
    "status": "sent",
    "from_address": "noreply@example.com",
    "envelope_from": "bounce@example.com",
    "to": ["user@example.com"],
    "subject": "Order Receipt",
    "created_at": "2026-01-11T16:00:00+00:00",
    "updated_at": "2026-01-11T16:00:01+00:00",
    "retry_count": 0,
    "error_message": null,
    "sent_at": "2026-01-11T16:00:01+00:00",
    "caller_id": "service-a",
    "smtp_auth_profile_id": null
  }
  ```

- Error responses:
  - 404: not found (also used when caller id does not match)
  - 500: internal error

### `GET /api/v1/emails/` (Queue stats)

- Purpose: Queue sizes for monitoring.
- Authorization: Requires `X-Caller-Id` and `QUEUE_STATS_ALLOWED_CALLERS` allowlist.
- Response schema (200 OK):

  ```json
  {
    "type": "object",
    "required": ["queue_size", "processing_size", "delayed_size", "dlq_size"],
    "properties": {
      "queue_size": {"type": "integer"},
      "processing_size": {"type": "integer"},
      "delayed_size": {"type": "integer"},
      "dlq_size": {"type": "integer"}
    }
  }
  ```

- Example response:

  ```json
  {
    "queue_size": 12,
    "processing_size": 3,
    "delayed_size": 1,
    "dlq_size": 0
  }
  ```

- Error responses:
  - 403: endpoint disabled or caller not authorized
  - 500: internal error

### Health/Readiness

- `/health`: dependency health (Redis + DB); returns 503 when unhealthy.
- `/ready` and `/readyz`: readiness check with dependency status; returns 503 when not ready.
- `/healthz`: liveness-only; always returns 200.
- Response schema (`/health`, `/ready`):

  ```json
  {
    "type": "object",
    "required": ["status", "checks"],
    "properties": {
      "status": {"type": "string"},
      "checks": {
        "type": "object",
        "required": ["redis", "database"],
        "properties": {
          "redis": {"type": "boolean"},
          "database": {"type": "boolean"}
        }
      }
    }
  }
  ```

- Example `/health` response (200 OK):

  ```json
  {
    "status": "healthy",
    "checks": {"redis": true, "database": true}
  }
  ```

- Example `/health` response (503 Service Unavailable):

  ```json
  {
    "status": "unhealthy",
    "checks": {"redis": false, "database": true}
  }
  ```

### Input Validation & Security Rules

- Address validation:
  - Request addresses use Pydantic `EmailStr` (syntactic email validation).
  - `ALLOWED_MAILFROM` allowlist is enforced against `envelope_from` in `email_service.create_email()`.
- Maximum sizes (current defaults):
  - `caller_id`: ≤ 256 chars
  - `idempotency_key`: ≤ 256 chars
  - `smtp_auth_profile_id`: ≤ 256 chars
  - `subject`: ≤ 1000 chars
  - `body`: ≤ 1,000,000 chars
  - `headers`: ≤ 50 entries; header name ≤ 128 chars; header value ≤ 2048 chars
  - `tags`: ≤ 50 entries; each tag ≤ 128 chars
  - `attachments`: ≤ 10; filename/content_type ≤ 255 chars; base64 ≤ 14,000,000 chars; decoded payload ≤ 10 MiB
- CR/LF injection protection (reject request when `\r` or `\n` appears):
  - Strings: `subject`, `from`, `envelope_from`, `reply_to`, `caller_id`, `idempotency_key`, `smtp_auth_profile_id`
  - Address lists: `to`, `cc`, `bcc`
  - Headers: header names and values
  - Attachments: filename/content_type (and filename forbids path separators / traversal tokens)
- HTML content:
  - This service does not sanitize HTML (it is an email relay and does not render content). When `html=true`, the HTML body is sent as-is, and a plain-text fallback is generated by stripping tags.
  - Callers are responsible for generating safe/appropriate HTML for recipients and for preventing injection in downstream renderers.
- Enforcement points (high-level checklist):
  - Schema validation: `app/schemas/email.py` (type checks + max lengths + CR/LF rejection)
  - Auth scoping: `X-Caller-Id` checks in `app/api/emails.py`
  - Envelope/header policy: `email_service.create_email()` (`ALLOWED_MAILFROM`, `ALLOWED_HEADERS`)
- Common failure modes:
  - 422: request schema validation (invalid email, size limits, CR/LF rejection in validated fields)
  - 400/403/409/500: business rules and operational failures (see endpoint-specific error mappings above)

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
  - Implemented: Prometheus exporter
    - API: `/metrics` (enable via `METRICS_ENABLED`, path via `METRICS_PATH`)
    - Worker: Prometheus server on `WORKER_METRICS_HOST:WORKER_METRICS_PORT` (queue size gauges updated every `WORKER_METRICS_POLL_INTERVAL_SECONDS`)
      - Multi-worker note: if you run multiple worker processes on the same host, either assign distinct `WORKER_METRICS_PORT` values per process, or enable Prometheus python-client multiprocess mode by setting `PROMETHEUS_MULTIPROC_DIR` and scraping a single shared endpoint.
    - Security: do not expose metrics endpoints publicly; use network access controls (private VNet, security groups, k8s NetworkPolicy) and/or an authenticated metrics gateway/proxy.
  - Optional: OpenTelemetry (not implemented in this repo yet).
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
- References (Microsoft Learn):
  - ACS domain verification and SPF/DKIM DNS records: [Add custom verified domains](https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/email/add-custom-verified-domains).
  - ACS domain-to-resource linking: [Connect a verified email domain to send email](https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/email/connect-email-communication-resource).
  - ACS sender authentication overview (SPF/DKIM): [Email domains and sender authentication for Azure Communication Services](https://learn.microsoft.com/en-us/azure/communication-services/concepts/email/email-domain-and-sender-authentication).
  - DMARC policy and reporting: [Use DMARC to validate email, setup steps](https://learn.microsoft.com/en-us/defender-office-365/email-authentication-dmarc-configure).

## Queue Design (Redis/Valkey)

### Keys

- `email:queue`: pending work (list of `email_id` strings)
- `email:processing`: in-flight work (list of `email_id` strings; populated via `BLMOVE(queue → processing)`)
- `email:delayed`: delayed retry schedule (sorted set of `email_id` strings)
  - Score (`ready_at`): Unix epoch seconds computed as `time.time() + delay_seconds`
- `email:dlq`: dead letter queue (list of JSON strings)
  - Schema (current): `email_id` (string), `error` (string), `dlq_at` (RFC3339 string), optional `retry_count` (int)
  - Example: `{"email_id":"...","error":"Permanent SMTP error: 550 ...","retry_count":3,"dlq_at":"2026-01-11T16:00:00+00:00"}`

- Delivery semantics: overall “at-least-once” (duplicates are possible; the worker should remain idempotent).

### Retention / TTL (production guidance)

- Redis Lists do not support per-item TTL. Key-level `EXPIRE` on `email:queue` / `email:processing` can drop unprocessed work and should only be used when you also have a reconciliation/outbox strategy.
- Recommended:
  - Implement a processing visibility timeout + reaper for `email:processing` (see Production readiness notes at the top of this document).
  - Periodically reconcile Redis queue entries against DB state and retention policy (e.g., remove ids that reference missing/deleted records).
  - Define a DLQ retention/archival policy (e.g., export to a durable store and cap in-Redis DLQ size via `LTRIM`).
  - Suggested starting defaults (tune per deployment):
    - `PROCESSING_VISIBILITY_TIMEOUT_SECONDS=300` (5 minutes)
    - `QUEUE_STALE_ITEM_MAX_AGE_HOURS=168` (7 days)
    - `DLQ_RETENTION_DAYS=7`

### Startup Scripts

`QueueService.connect()` registers Lua scripts used by:
- Move to DLQ (atomic): `LREM processing email_id` → if removed, `LPUSH dlq dlq_item`
- Requeue (atomic): `LREM processing email_id` → if removed, `LPUSH queue email_id`
- Requeue delayed (atomic): `LREM processing email_id` → if removed, `ZADD delayed ready_at email_id`
- Move ready delayed items: `ZRANGEBYSCORE delayed 0..now LIMIT 0..N` → `ZREM` → `LPUSH queue` for each moved id

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
    - ACS note: create and manage SMTP usernames in Azure Portal → “SMTP Usernames”; the value can be freeform or email-form (email-form requires a linked domain). See [Microsoft Learn: SMTP authentication](https://learn.microsoft.com/azure/communication-services/quickstarts/email/send-email-smtp/smtp-authentication).
    - Examples: `relay-app` (freeform), `noreply@example.com` (email-form)
  - `SMTP_PASSWORD` (required, string; secret)
    - ACS note: use a client secret of the Microsoft Entra application linked to the SMTP username.
  - Auth: this repo uses SMTP AUTH (username + password). For ACS, the service uses the linked Microsoft Entra application to obtain an access token internally; this repo does not implement the XOAUTH2 SASL mechanism.
  - Azure prerequisites: creating SMTP usernames and linking Entra apps requires Azure RBAC on the Communication Services resource; recommended built-in role: `Communication and Email Service Owner` (or a custom role with at least `Microsoft.Communication/CommunicationServices/read`, `Microsoft.Communication/CommunicationServices/write`, and `Microsoft.Communication/EmailServices/write` as needed).
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
  - References:
    - [Email SMTP overview](https://learn.microsoft.com/en-us/azure/communication-services/concepts/email/email-smtp-overview)
    - [Send an email using SMTP](https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/email/send-email-smtp/send-email-smtp)
    - [Troubleshoot outbound SMTP connectivity in Azure](https://learn.microsoft.com/en-us/azure/virtual-network/troubleshoot-outbound-smtp-connectivity)
    - [Azure-Samples/acs-email-relay-quickstart](https://github.com/Azure-Samples/acs-email-relay-quickstart)
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
- Metrics:
  - `METRICS_ENABLED` (optional, bool, default: `false`)
  - `METRICS_PATH` (optional, string, default: `/metrics`)
  - Worker exporter: `WORKER_METRICS_HOST` (optional, string, default: `127.0.0.1`), `WORKER_METRICS_PORT` (optional, int, default: `8001`), `WORKER_METRICS_POLL_INTERVAL_SECONDS` (optional, int, default: `15`)
- Idempotency (suggested future option):
  - `IDEMPOTENCY_WINDOW_HOURS` (optional, int, default: `24`)
    - Implementation status: not implemented in this repo (planned). Current behavior is “as long as the email record exists” (DB uniqueness on `caller_id + idempotency_key`).
    - Target behavior if implemented: defines the time window during which a given `caller_id + idempotency_key` pair is treated as deduplicated.
    - Within the window: requests with the same `caller_id + idempotency_key` return the existing record (and should not enqueue again).
    - After the window: the same `idempotency_key` can be reused to create a new record.
    - Data retention interaction:
      - If your retention is shorter than the window, idempotency can break early (records/keys disappear before the window ends).
      - If your retention is longer than the window, you likely need explicit key expiry/cleanup (or a separate idempotency store) so keys can expire before the underlying email records are deleted.
- Operations (suggested future options):
  - Queue retention:
    - `PROCESSING_VISIBILITY_TIMEOUT_SECONDS` (suggested default: `300`)
    - `QUEUE_STALE_ITEM_MAX_AGE_HOURS` (suggested default: `168`)
    - `DLQ_RETENTION_DAYS` (suggested default: `7`)
    - `DLQ_MAX_ITEMS` (optional safety cap; consider archival first)
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
        lock_key = f"email:lock:{email_id}"
        token = uuid4().hex
        if not redis.set(lock_key, token, nx=True, ex=60):
            return "locked"  # skip/requeue/backoff

        try:
            send_email(email_id)
        finally:
            redis.eval(
                "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0",
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
    - How to measure (example approach):
      - Load test tools: k6 or Locust (generate `POST /api/v1/emails/` traffic).
      - Scenarios: (1) no attachments, plain text; (2) HTML; (3) with attachments (to validate size/latency impact).
      - Metrics collection:
        - API: scrape `/metrics` (when enabled) and track request rate + latency.
        - Worker: scrape worker metrics and derive throughput from `email_worker_result_total{result="sent"}` (and failure/retry rates from labeled counters).
      - Example k6 script (no attachments):

        ```javascript
        import http from "k6/http";
        import { check, sleep } from "k6";

        export const options = { vus: 10, duration: "1m" };

        export default function () {
          const url = `${__ENV.BASE_URL}/api/v1/emails/`;
          const payload = JSON.stringify({
            caller_id: "load-test",
            idempotency_key: `loadtest-${__VU}-${__ITER}-${Date.now()}`,
            from: "noreply@example.com",
            to: ["user@example.com"],
            subject: "Load test",
            body: "Hello",
            html: false
          });
          const params = {
            headers: {
              "Content-Type": "application/json",
              "X-Caller-Id": "load-test"
            }
          };

          const res = http.post(url, payload, params);
          check(res, { "accepted": (r) => r.status === 202 });
          sleep(0.1);
        }
        ```

    - Tune DB indexes, connection pooling, worker concurrency, and retry/backoff settings per environment.
  - API deployment: run multiple API instances behind a load balancer; ensure all instances share the same DB and Redis.

## Operational Runbook (Outline)

> Production deployment is prohibited until every checklist item below is completed and validated.
>
> Tracked in [issue #8](https://github.com/seonghobae/azure-email-communication-service-sender/issues/8) and treated as a production blocker (the issue must include assigned owners, target completion dates, verification steps, and a sign-off confirmation for each item).
>
> Completion checklist:
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
    - `redis-cli --raw LPOP email:dlq | jq -r '.email_id' | xargs -I {} sh -c 'redis-cli LPUSH email:queue \"$1\"' _ {}`

- Runbook hygiene: perform periodic game days (e.g., quarterly) to validate procedures and update thresholds.
