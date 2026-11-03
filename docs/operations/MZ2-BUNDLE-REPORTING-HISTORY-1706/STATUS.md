# MZ2-BUNDLE-REPORTING-HISTORY-1706 — 2026-11-03 checkpoint

Issue: #1706. Repo: AMASI-SA/AMASI-SA. Branch: `codex/mz2-bundle-historical-reporting-1706-20261103`.
Baseline: production `e01d4d3920fc5bf4a7280757ca2030c30b4be9c8` (checked before branch creation).

## User's acceptance
- Bundle cost reporting (top-selling products **and** executive profit) must use selected customer choices and historical evidence.
- Shawl base SAR15, optional rose SAR40, independent external-supplier embroidery SAR10 only on name=Yes.
- No mutation of original supplier preparation, stock, service bindings, invoices, cash ledger, Qoyod, old orders or historical price without approved proof.
- Owner supplied Production API evidence: order 297657209, quantity1, missing bundle snapshot; 15 units of AMS13281 in the Nov2–3 report cost-null; executive total=product-report total 18,353 SAR with 46 incomplete orders.
- Exact provider order-line option row IDs differ from current catalog IDs. Raw private custom fields must not appear in logs or PR.

## Completed — source-only, safe milestone 1
- Added `backend/bundle_historical_reporting_audit.py`: tenant-caller-bound persisted provider line matching, source creation validation, duplicate refusal, strict source-versus-canonical option fingerprints; returns only hashed evidence and sanitized metadata, **never price**. A stored Salla/canonical match is **not** an immutable first-ingestion proof.
- Added owner-only `GET /api/orders-v2/{order_number}/bundle-financial-source-audit` (optional exact order_item_id). Read-only, source tenant-scoped, composite product check, no mutation.
- Added owner-only `POST /api/orders-v2/{order_number}/bundle-financial-cost-dry-run`: explicit sale-time cost attestation, immutable source digest, valid effective date, exact selected value ID coverage, nonnegative two-decimal SAR prices, strict external supplier/service class. Returns reviewed **proposal only**, including per-unit and per-line totals and SHA manifest; `ready_for_apply=false`, zero writes.
- Added tests for source selection ID mismatch, text privacy, attestation tampering, future price timing, missing/duplicate selections, 65 SAR one-unit proposal, redacted text never billed, and manifest identity.
- Added `backend/tests/test_bundle_historical_reporting_audit.py` to test two distinct option/row ID semantics, private text absence, changed values, invalid timestamp, duplicates, missing original, no historic pricing.
- Registered the suite as mandatory in `.github/workflows/g47-focused-integration.yml` (exact-head; skipped/failed tests blocked).
- Does not install dependencies, access live Production Mongo, create order-cost corrections, or expose a cost-application endpoint. Dry-run POST accepts owner attestation but never calls an accounting/fulfillment write.

## Important limitations and next stages
- This is **NOT** the final cost correction; the proposal endpoint requires an independently reviewed sale-time cost-basis reference. The next gate is authoritative confirmation of source compatibility, **then** a separate idempotent reporting-only transaction gated by an approved manifest.
- Current Mezan catalog or a product option display label is not automatically historic supplier-cost proof. A refreshed Salla snapshot can be newer than original sale. The audit explicitly returns `immutable_first_ingestion_proven=false`. Never backfill from current catalog or an unsealed source match.
- Only the specific order can be attested; the other 14 observed AMS13281 units require their own sale evidence.
- G47 CI must be read after Draft PR created. No claim of passing tests or Production deploy until verified.
- Remain isolated from concurrent shipping/operational financial branches.

## Safe next action
1. Draft PR #1708 open to hotfix/prod-snap-meta-final; inspect exact-head G47 and security CI for the newest commit.
2. Fix any audit failures on this task branch only.
3. Implement evidence-based dry-run (before/after cost) and transaction-safe existing correction collection, after establishing original sale time cost proof. Re-run cost tests and dashboard reconciliation.
4. Explicit separate approval for Production data correction and v5 A/B release. No production merge or publish at this checkpoint.

Production changed by this task: **NO**.
