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
- Added `backend/bundle_historical_reporting_audit.py`: tenant-caller-bound original provider line matching, original creation validation, duplicate refusal, strict source-versus-canonical option fingerprints; returns only hashed proof and sanitized metadata, **never price**.
- Added owner-only `GET /api/orders-v2/{order_number}/bundle-financial-source-audit` (optional exact order_item_id). Read-only, source tenant-scoped, composite product check, no mutation.
- Added `backend/tests/test_bundle_historical_reporting_audit.py` to test two distinct option/row ID semantics, private text absence, changed values, invalid timestamp, duplicates, missing original, no historic pricing.
- Registered the suite as mandatory in `.github/workflows/g47-focused-integration.yml` (exact-head; skipped/failed tests blocked).
- Does not install dependencies, access live Production Mongo, create order-cost corrections, or expose a cost-application endpoint.

## Important limitations and next stages
- This is **NOT** the final cost correction; the next gate is independent sale-time cost provenance plus an explicitly reviewed dry-run manifest and idempotent reporting-only correction.
- Current Mezan catalog or a product option display label is not automatically historic supplier-cost proof. Never backfill from current catalog.
- Only the specific order can be attested; the other 14 observed AMS13281 units require their own sale evidence.
- G47 CI must be read after Draft PR created. No claim of passing tests or Production deploy until verified.
- Remain isolated from concurrent shipping/operational financial branches.

## Safe next action
1. Open draft PR to hotfix/prod-snap-meta-final and inspect G47 and security CI.
2. Fix any audit failures on this task branch only.
3. Implement evidence-based dry-run (before/after cost) and transaction-safe existing correction collection, after establishing original sale time cost proof. Re-run cost tests and dashboard reconciliation.
4. Explicit separate approval for Production data correction and v5 A/B release. No production merge or publish at this checkpoint.

Production changed by this task: **NO**.
