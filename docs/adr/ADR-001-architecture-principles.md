# ADR-001 — Mezan Architecture Principles

**Status:** Accepted (2026-06-25)
**Scope:** All new code under `/app/backend/integrations/` and any
future cross-cutting refactor. Pre-existing modules continue to
operate as-is until explicitly migrated (per principle #1, Additive).

---

## Vision

Mezan is no longer just an accounting dashboard. It is becoming an
**E-Commerce Accounting Platform** that integrates with multiple
external systems (Salla, Qoyod, BNPLs, ad platforms, shipping
companies, future payment gateways) without ever requiring a
re-architecture.

ADR-001 captures the engineering rules that protect that vision.

---

## The 14 Principles

### 1. Additive Architecture
Any new development is an independent addition. No deletion,
replacement, or breaking of existing parts without explicit approval.

### 2. Zero Downtime
Each new layer is deployable without service interruption or
disruption to existing users.

### 3. Feature Flags
Every new module ships with an enable/disable flag (env-var or
DB-stored). Disabling a flag must fully neutralise the module.

### 4. Canonical Domain
Inside Mezan we never depend on external system shapes. Every
provider payload is mapped to an internal canonical model:
`Order`, `Product`, `Customer`, `Payment`, `Shipment`, `Invoice`,
`Refund`, `Settlement`, etc.

### 5. Input ≠ Output Connectors
- **Input Connectors** (Salla, Make.com) bring data into Mezan.
- **Output Connectors** (Qoyod) receive data after processing.
They have different lifecycles, contracts, error semantics, and
must not share a single base class.

### 6. Layered Architecture
All external data flows strictly through:
`Input → Validation → Normalization → Canonical → Business Rules
→ Accounting Engine → Output`
No stage may be skipped. Events may terminate before the final
stage (e.g., a customer-update event that has no accounting effect)
but they cannot reach a later stage without traversing earlier ones.

### 7. Backward Compatibility
New code preserves full compatibility with the existing system.
Modifications to legacy tables or APIs require review and a
documented migration plan.

### 8. Event Driven
Every external event must be:
- Traceable (`trace_id` + structured logs)
- Replayable (raw payload retained)
- Auditable (immutable history of stage transitions)
- Never lost (append-only inbox)

### 9. Single Source of Truth (SSOT)
Every value has exactly one authoritative source. Other layers read
from it; they do not store duplicates. Concrete examples already in
production:
- Currency of an ads account → `ads_accounts.currency_native` only.
- Shipping cost → `shipping_cost_ssot.py`.
- Snapchat spend → `ads_daily.spend_native` (never recomputed elsewhere).

### 10. Idempotency by Design
Every external event carries an `idempotency_key`. Re-processing the
same event must never produce duplicate accounting effects (no
duplicate invoices, duplicate ledger entries, or duplicate notifications).

### 11. Multi-Tenant Isolation
Every new collection carries `user_id`. Every query filters by it.
Lock-in tests prove no cross-tenant read is possible.
**MVP exception:** Qoyod Invoice MVP runs single-tenant with
`user_id="main"` — the schema is multi-tenant ready; the policy
will simply be lifted later.

### 12. Reversibility
Every accounting effect has a documented compensating action. No
"final, irreversible" writes to the ledger.

### 13. Versioning Discipline
Connectors and canonical DTOs carry an explicit version
(`schema_version` / connector `version`). Migrations support N+1
schemas in parallel before retiring old ones.

### 14. Secrets Discipline
API keys, OAuth tokens, and signing secrets live in a single
encrypted store (one collection per concern). They are never logged,
never returned in API responses (only fingerprints), and key
rotation is supported via primary + old key (`MultiFernet`).

---

## How to comply

Every new module's `README.md` (or top-level docstring) must
explicitly map its design choices to the principles above by number.
Pull requests that change architecture must reference ADR-001 and,
when warranted, propose a new ADR.
