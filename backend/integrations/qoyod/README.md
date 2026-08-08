# Qoyod Output Connector

> **Maintenance status: CLOSED / STABLE — 2026-08-08**
>
> The Qoyod integration is considered operationally complete. Its normal path is
> automatic Salla → Qoyod invoicing and receipt recording. Do not reopen, refactor,
> redesign, or otherwise modify this area as routine development.

## Change gate

Changes to Qoyod code, pages, settings, jobs, schemas, mappings, or integration
behavior are allowed only when one of these conditions is explicitly present:

1. The owner requests a new Qoyod feature.
2. The owner requests a change to an existing Qoyod feature.
3. A verified production problem requires a fix.

No other cleanup, modernization, speculative refactor, dependency migration, or
opportunistic change belongs in the Qoyod scope.

Before an allowed change is merged:

- State which of the three conditions authorizes the change.
- Preserve invoice/receipt idempotency and never create duplicate Qoyod documents.
- Verify the automatic send path and reconciliation report.
- Keep unsafe or ambiguous orders in exceptions; do not retry blindly.
- Do not restore the retired daily manual-send workflow.
- Use a focused PR and avoid unrelated Qoyod edits.

## Locked operating policy

- Automatic sending is the only normal production path.
- Eligible orders are invoiced once when they reach the configured execution state.
- Invoices and receipts must remain idempotent.
- Manual or bulk resending is not a routine operation.
- A failed or unsafe order remains visible in exceptions for diagnosis.
- Reconciliation is read-only toward Qoyod; local repair markers may only be rebuilt
  from confirmed Qoyod invoices.
- Qoyod secrets must remain encrypted and must never be exposed in UI, logs, source,
  screenshots, or pull requests.

## Operational baseline at closure

Verified on 2026-08-08 after the final production deployment:

- Automatic worker: running.
- Eligible orders waiting to send: 0.
- Failed orders requiring review: 0.
- Eligible unsent orders in reconciliation: 0.
- Matched Mezan ↔ Qoyod invoices: 1031.
- Locally synchronized Qoyod invoices: 1204.

These figures are a closure snapshot, not permanent expected totals. Future totals
will grow while the policy and invariants above remain fixed.

---

## Purpose

Push Salla orders to Qoyod invoices and receipts while preserving strict
idempotency, auditability, tenant isolation, encrypted credentials, and safe
reconciliation.

## Files

| Module           | Responsibility |
|------------------|----------------|
| `crypto.py`      | Fernet encrypt/decrypt for the Qoyod API key (ADR-001 #14). |
| `models.py`      | Pydantic models + `ensure_qoyod_indexes()` for Qoyod collections. |
| `credentials.py` | The only public entry point to the encrypted credential store. |
| `api_client.py`  | Thin httpx wrapper around Qoyod REST API. Stateless; one instance per request. |

## Collections

- `qoyod_settings` — connector configuration.
- `qoyod_credentials` — encrypted API key store.
- `qoyod_inbox` — append-only raw webhook events.
- `qoyod_invoices` — synchronized and sent invoice records.
- `qoyod_products_mapping` — SKU → Qoyod product id.
- `qoyod_customers_mapping` — customer identity → Qoyod contact id.

## Environment variables

```text
QOYOD_TOKEN_ENC_KEY      Fernet key for API key encryption
QOYOD_TOKEN_ENC_KEY_OLD  optional rotation key (decrypt-only)
QOYOD_API_BASE           https://www.qoyod.com/api/2.0
QOYOD_WEBHOOK_TOKEN      shared secret in Make.com webhook URL
```

## Core invariants

- **Feature control** — `qoyod_settings.enabled` controls the connector.
- **Canonical payloads** — inbox events retain typed canonical payloads.
- **Input ≠ Output** — this is an output connector (Mezan → Qoyod).
- **Event-driven audit** — `qoyod_inbox` is append-only with pipeline stage data.
- **Idempotency** — invoice and receipt creation uses stable order identities.
- **Tenant isolation** — Qoyod records are scoped by `user_id`.
- **Schema versioning** — persisted models carry explicit schema versions.
- **Secret protection** — credentials use the encrypted store and redacted client
  representations.
