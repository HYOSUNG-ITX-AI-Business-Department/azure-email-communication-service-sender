# Azure Email Communication Service Sender — PRD

## Summary

This project provides a reliable “sender API + worker” system that lets internal services submit email requests via a REST API and have them delivered via Azure Communication Services (ACS) SMTP Relay, with multi-tenant isolation, idempotency, persistence, retries, state transitions, and a dead-letter queue.

## Problem

Teams need a reliable, auditable way to send transactional email without embedding SMTP logic in every service. They also need visibility into delivery attempts and failures, plus safe defaults around envelope/header separation and idempotency to reduce duplicates.

## Goals

- Provide a REST API for submitting email send requests.
- Persist email requests and delivery state transitions.
- Deliver email via ACS SMTP Relay with correct envelope/header handling.
- Support retries with exponential backoff and DLQ routing.
- Support multi-tenant isolation via `caller_id` and per-caller idempotency.
- Provide basic health/readiness endpoints for operations.

## Non-goals

- A full user-facing email product (templates, campaigns, analytics, UI).
- A generic authN/authZ system (assumes trusted upstream identity injection).
- A complete production migration framework (recommended via Alembic).

## Users & Use Cases

- **Internal microservices** submitting transactional emails (receipts, alerts, password resets).
- **Operators/SREs** monitoring queue sizes, failures, and service health.

## Functional Requirements

### Email submission API

- Accept email requests with:
  - `from` (header From)
  - `envelope_from` (SMTP MAIL FROM; optional, defaults to `from`)
  - `to` (required) and optional `cc`, `bcc`
  - `subject`, `body`, `html`
  - optional `reply_to`, `headers` (allowlisted), `tags`
  - `caller_id` (required) and optional `idempotency_key`
- Deployment/setup requirement (ACS Email):
  - Verify a sending domain in ACS by publishing the required DNS records (SPF/DKIM): [Add custom verified domains](https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/email/add-custom-verified-domains).
  - Connect the verified domain to the Communication Services resource used for SMTP sending: [Connect a verified email domain to send email](https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/email/connect-email-communication-resource).
  - Configure `ALLOWED_MAILFROM` to include only addresses under verified/connected domains; requests must validate `envelope_from` against `ALLOWED_MAILFROM` and reject when it does not match.
  - Operational check: ensure the domain and SMTP username status is “Ready to use” before enabling production traffic.
- Require `X-Caller-Id` header (trusted upstream) and reject when it does not match `caller_id`.
- Return an email id and current status on success.
- Idempotency:
  - For a given `caller_id` and `idempotency_key`, reuse the existing record when payload matches.
  - Reject reuse with different payloads.
  - Avoid duplicate queueing on idempotency replays.
  - Payload match definition (canonical comparison):
    - Compare: `from`, `envelope_from`, `to`/`cc`/`bcc` (case-insensitive address lists), `subject`, `body`, `html`, `reply_to`, `smtp_auth_profile_id`, `headers` (case-insensitive header names), `tags`, `attachments`.
    - Exclude: server-generated timestamps, delivery state, retry counters, and other volatile fields.
  - Error semantics:
    - On mismatch: return 409 Conflict (error format: `{"detail":"..."}`).
    - On match: return the existing record; do not enqueue again unless the existing record is still `pending`.
  - Retention:
    - Idempotency is retained at least for the email record retention window (recommended minimum: 7 days; configurable; align with compliance needs).

### Status API

- Provide a read endpoint: `GET /api/v1/emails/{email_id}`.
- Enforce caller scoping: require `X-Caller-Id` and ensure only the owning caller can access its emails.
- Status values: `pending`, `queued`, `sending`, `sent`, `failed`, `dlq`.
- Response fields (minimum):
  - Identifiers: `email_id`, `caller_id`
  - State: `status`, `retry_count`, optional `error_message`
  - Timestamps: `created_at`, `updated_at`, optional `sent_at`
  - Key metadata: `from_address`, `envelope_from`, `to`, `subject`, optional `smtp_auth_profile_id`
- HTTP responses:
  - 200: returns the status payload
  - 404: email not found (also used for caller mismatch to avoid leaking existence)
  - 500: internal error

### Delivery worker

- Consume email ids from a Redis/Valkey queue and send via ACS SMTP Relay.
- Update statuses through the lifecycle (`pending` → `queued` → `sending` → `sent` / `failed` / `dlq`).
- Record all status transitions in an `audit_log` (JSON list) for debugging and compliance (timestamp + status + message + retry metadata).
- Retry transient failures up to `MAX_RETRIES` with exponential backoff (optional cap/jitter).
- Move permanently failing items or items that exceed retries to DLQ.

## Non-Functional Requirements

### Reliability

- SLO targets (initial; tune per environment):
  - API availability: ≥ 99.9% (monthly).
  - Delivery success rate (sent / total): ≥ 99.9% (24h rolling; see Success Metrics).
- Recovery targets (initial; tune per environment):
  - RTO: resume accepting requests and processing queued emails within 15 minutes after a restart or dependency outage (assuming DB/Redis are restored).
  - RPO: 0 for persisted email records (DB); queue recovery depends on Redis persistence (configure AOF and/or a DB→queue reconciliation strategy).
- Dependency failure behavior:
  - If DB is unavailable: `/health` and `/ready` must fail and the API must reject submissions until DB recovers.
  - If Redis is unavailable: `/health` and `/ready` must fail; submissions must not be accepted unless the system can guarantee later enqueue (outbox/reconciliation; see TRD production readiness/runbook).
- Backup/DR:
  - Database backups + PITR enabled; regularly rehearse restores (at least monthly in a non-production environment).
  - Redis persistence configured to meet RPO; have a documented recovery procedure (see TRD runbook).

### Security

- Treat `X-Caller-Id` as an authenticated identity only when set by a trusted upstream.
- Enforce `ALLOWED_MAILFROM` allowlist for envelope sender addresses.
- Enforce an allowlist for custom headers; reject CR/LF injection in string fields.
- Restrict queue stats endpoint to admin/ops callers via `QUEUE_STATS_ALLOWED_CALLERS`.

### Observability

- Detailed specification: see [TRD Observability](TRD.md#observability).
- Health/readiness endpoints:
  - `/health` and `/ready` check Redis and DB dependencies and return 503 when unhealthy.
  - `/healthz` is liveness-only (always 200).
  - Recommended: per-dependency timeouts (e.g., 1s) and no retries for these endpoints.
- Metrics (recommended): submission rate, queue depth, retry/DLQ rates, delivery success rate, and latency p50/p90/p99.
- Logging: prefer structured JSON logs with level guidance and PII masking.
- Tracing (optional): OpenTelemetry with propagated trace context.

## Success Metrics

Initial targets (tune per environment and SMTP quotas):
- Successful delivery rate (sent / total): ≥ 99.9% (24h rolling).
- Retry rate: ≤ 5% (24h rolling).
- DLQ rate (dlq / total): ≤ 0.1% (24h rolling).
- Mean time to recovery (MTTR) for dependency outages (Redis/DB/SMTP): ≤ 15 minutes (P1 incidents).
- Duplicate-send incidents: ideal 0 (alert threshold: < 1/month).

Measurement and alerting (recommended):
- Collection: derive delivery/retry/DLQ rates from DB statuses and worker logs; track queue sizes via Redis (or the queue stats endpoint).
- Frequency: collect at 1-minute granularity and review trends regularly.
- Dashboards/alerts: implement dashboards for delivery rate, retry/DLQ rate, queue sizes, and latency; alert when targets/thresholds are breached (see TRD runbook for playbooks).

## References

- ACS SMTP auth credentials: [Set up SMTP authentication for sending emails](https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/email/send-email-smtp/smtp-authentication).
- ACS SMTP overview: [Email SMTP overview](https://learn.microsoft.com/en-us/azure/communication-services/concepts/email/email-smtp-overview).
- Send email via SMTP: [Send an email using SMTP](https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/email/send-email-smtp/send-email-smtp).
- Network troubleshooting: [Troubleshoot outbound SMTP connectivity in Azure](https://learn.microsoft.com/en-us/azure/virtual-network/troubleshoot-outbound-smtp-connectivity).
- Sample: [Azure-Samples/acs-email-relay-quickstart](https://github.com/Azure-Samples/acs-email-relay-quickstart).

## Open Questions

- Do we require a formal “admin/ops identity” mechanism beyond caller allowlists? (tracked in #6)
- Should idempotency replays return 200 vs 202 when an email is already sent? (tracked in #7)
- Migration strategy (Alembic) and operational runbooks for production deployments. (tracked in #8; production blocker; see [TRD Operational Runbook](TRD.md#operational-runbook-outline))
