# Azure Email Communication Service Sender — PRD

## Summary

This project provides a simple “sender API + worker” system that lets internal services submit email requests via a REST API and have them delivered via Azure Communication Services (ACS) SMTP Relay, with persistence, retries, and a dead-letter queue.

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
- Require `X-Caller-Id` header (trusted upstream) and reject when it does not match `caller_id`.
- Return an email id and current status on success.
- Idempotency:
  - For a given `caller_id` and `idempotency_key`, reuse the existing record when payload matches.
  - Reject reuse with different payloads.
  - Avoid duplicate queueing on idempotency replays.

### Status API

- Provide a read endpoint that returns the current status and metadata for a given email id.
- Enforce caller scoping (only the authenticated caller may access its emails).

### Delivery worker

- Consume email ids from a Redis/Valkey queue and send via ACS SMTP Relay.
- Update statuses through the lifecycle (`pending` → `queued` → `sending` → `sent` / `failed` / `dlq`).
- Retry transient failures up to `MAX_RETRIES` with exponential backoff (optional cap/jitter).
- Move permanently failing items or items that exceed retries to DLQ.

## Non-Functional Requirements

### Reliability

- Persist state to support restarts and auditing.
- Ensure failures do not silently drop requests.

### Security

- Treat `X-Caller-Id` as an authenticated identity only when set by a trusted upstream.
- Enforce `ALLOWED_MAILFROM` allowlist for envelope sender addresses.
- Enforce an allowlist for custom headers; reject CR/LF injection in string fields.
- Restrict queue stats endpoint to admin/ops callers via `QUEUE_STATS_ALLOWED_CALLERS`.

### Observability

- Log key transitions and errors.
- Provide health/readiness endpoints:
  - `/health` and `/ready` reflect Redis+DB dependency status (503 when unhealthy).
  - `/healthz` is liveness-only (always 200).

## Success Metrics

- Successful delivery rate (sent / total).
- Retry rate and DLQ volume.
- Mean time to recovery (MTTR) for dependency outages (Redis/DB/SMTP).
- Duplicate-send incidents (should be near zero given idempotency + queue discipline).

## Open Questions

- Do we require a formal “admin/ops identity” mechanism beyond caller allowlists? (tracked in #6)
- Should idempotency replays return 200 vs 202 when an email is already sent? (tracked in #7)
- Migration strategy (Alembic) and operational runbooks for production deployments. (tracked in #8)
