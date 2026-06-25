# Qoyod Output Connector — MVP

**Goal:** Push Salla orders → Qoyod invoices (+ receipts) before end of June 2026.

**Status:** Day 1 — Foundation complete.

---

## Files

| Module           | Responsibility |
|------------------|----------------|
| `crypto.py`      | Fernet encrypt/decrypt for the Qoyod API key (ADR-001 #14). |
| `models.py`      | Pydantic models + `ensure_qoyod_indexes()` for the 5 new collections. |
| `credentials.py` | The ONLY public entry point to the encrypted credential store. |
| `api_client.py`  | Thin httpx wrapper around Qoyod REST API. Stateless; one instance per request. |

## Collections (all `qoyod_*` — no collision with existing)

- `qoyod_settings`            — single-row connector config
- `qoyod_credentials`         — encrypted API key store
- `qoyod_inbox`               — append-only raw webhook events (ADR-001 #8)
- `qoyod_invoices`            — one row per Salla order processed
- `qoyod_products_mapping`    — SKU → Qoyod product id
- `qoyod_customers_mapping`   — phone/email → Qoyod contact id

## Env vars (in `backend/.env`)

```
QOYOD_TOKEN_ENC_KEY      Fernet key for API key encryption
QOYOD_TOKEN_ENC_KEY_OLD  optional rotation key (decrypt-only)
QOYOD_API_BASE           https://www.qoyod.com/api/2.0
QOYOD_WEBHOOK_TOKEN      shared secret in Make.com webhook URL
```

## ADR-001 compliance map

- **#1 Additive** — no existing collection or route touched.
- **#3 Feature Flag** — `qoyod_settings.enabled` defaults to `false`.
- **#4 Canonical** — `qoyod_inbox.canonical_payload` is typed.
- **#5 Input ≠ Output** — this is an OUTPUT connector (Mezan → Qoyod).
- **#8 Event Driven** — `qoyod_inbox` append-only with `pipeline_stage`.
- **#10 Idempotency** — unique index on `(user_id, idempotency_key)`;
  Qoyod POSTs carry `Idempotency-Key` header.
- **#11 Tenant** — every model has `user_id` (MVP uses `"main"`).
- **#13 Versioning** — every model carries `schema_version: int = 1`.
- **#14 Secrets** — Fernet store + `__repr__` redaction in client.

## Roadmap

- ✅ Day 1: Foundation (this commit).
- ⏳ Day 2: Settings page + Test-connection + Catalogs proxies.
- ⏳ Day 3: Webhook + Inbox + Normalization stages.
- ⏳ Day 4: Pipeline stages 4a–4d (Customer / Products / Invoice / Receipt).
- ⏳ Day 5: Monitoring page + Retry + E2E + Lock-in tests.
