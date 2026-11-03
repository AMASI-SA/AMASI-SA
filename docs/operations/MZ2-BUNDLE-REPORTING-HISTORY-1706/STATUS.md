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

## Milestone 2 — implemented and acceptance-tested, Production unchanged

- **Reviewed code commit (source A checkpoint):** `3a08b70d785a1444644fe3f8734b052071e6fbea`.
- New `backend/bundle_historical_reporting_repair.py` supports a bounded, tenant-scoped owner-attested expected-cost repair of **one explicit order line at a time**, not an auto-backfill.
- The owner-only authenticated plan endpoint `POST /api/orders-v2/{order_number}/bundle-financial-cost-repair-plan` produces a manifest SHA and stripped public summary without touching production data. Existing previous missing cost is `null`, **never** silently treated as zero.
- The owner-only apply endpoint `POST /api/orders-v2/{order_number}/bundle-financial-cost-repair-apply` requires a fresh exact manifest SHA and explicit approval phrase, AND `MZ2_HISTORICAL_BUNDLE_REPORTING_APPLY_ENABLED=1`; that flag remains **disabled by default**. Do not enable without independently reviewed owner authorization.
- Only the existing immutable `mezan_order_cost_corrections_v1` and shared `mezan_product_cost_revisions_v2` may be written, atomically, via `_RepairDB`. No supplier, inventory, invoice, journal, Qoyod, order status or original Salla raw modifications.
- Report loader checks correction fingerprint with reconstructed canonical `_id` (the bounded Mongo projection omits it), original authority hash, quantity, product identity, and line fingerprint. The same authority feeds `product_cost_v2.total`, product rows, and the executive profit calculation. Refunds/cancellations are applied once by existing policy.
- The correction source is labeled `historical_owner_attested_bundle` (expected only), explicitly `owner_attested_not_provider_invoice=true`. Persisted Salla/canonical source match is not proof of immutable first ingestion; this remains `immutable_first_ingestion_proven=false`.
- New `backend/tests/test_bundle_historical_reporting_repair.py` contains disposable real-Mongo transaction, SHA re-approval, replay/no double-count, rejection of conflicting snapshots/operational plans, foreign-tenant denial, tamper detection, and dashboard total/product-row equality. New suite is mandatory in G47.

### Exact-source CI results for code commit `3a08b70d...`
- All **11/11** GitHub workflow runs SUCCESS on that exact SHA, including CodeQL, Security Gate, Dashboard runtime adoption, Operational Stock Eligibility, Products V2, Mezan Release Readiness, Operational Balance Web, G47 and supporting workflows.
- G47 integration: **327 cases PASSED plus 127 subtests**, zero skips/failures/errors.
- G47 historical cost/report acceptance: **457 cases PASSED**, zero skips/failures/errors, including new real-Mongo tests and first-ingestion regressions.
- Frontend G47 run SUCCESS.
- This status-file checkpoint commit is documentation only; final release readiness must validate its exact head separately if proposed for merge or publish.

### What remains / safeguards
1. Obtain independently reviewed **sale-time cost basis** for the exact order and each selected option; an editable provider/catalog label or newer refreshed snapshot alone cannot certify a historical price.
2. Inspect and approve an exact owner-scoped **dry run** against actual Production via authenticated owner tools. Order `297657209` is one unit; the 15-period AMS13281 total is **not** 15 × SAR65 unless each original sale proves identical selections.
3. If explicitly authorized later, perform v5 source A / reviewed intent-only B, merge and publish with the guard; validate 3/3 before enabling a separately approved reporting-only data correction. No production write is justified by a code CI pass alone.

**Current task Production writes: NO. New deployment: NO. Existing PR #1695 deployment unchanged.**

## Milestone 3 — owner-approved current costs for all unpriced sold units (2026-11-03)

**Owner policy decision:** Costs currently configured in Mezan apply to **every sold unit of the product in profitability reports**, including orders created before cost setup. In this product the owner confirms that the original SAR15 shawl + optional SAR40 rose + optional SAR10 independent external-supplier embroidery were already valid at sale time. Each option is charged **only on order lines where the buyer chose it**, quantity multiplies once.

Implementation checkpoint source (before this documentation-only commit): `6bfff5c111a8e169d75ed174e7cdf98611b33d0c`. This supersedes the earlier requirement to manually attest a historical price for each older **unpriced** unit, for **reporting estimates** only. It does not rewrite any persisted accounting, actual or frozen cost.

- `backend/current_bundle_reporting_estimate.py`: bounded tenant-scoped read-only current-catalog projection for **only** composite order lines with missing authority. Current configuration for physical parent, selected recipe products/resources, option-linked external supplier services and SAR costs. Original Salla option-row IDs may differ from catalog IDs; recover a price choice only via an exact ID or uniquely matching catalog name/value, never transfer the inferred choice to stock/supplier execution. Do not guess unknown, missing or conflicting choices; fail closed.
- `backend/order_cost_authority.py`: exact, existing persisted frozen/actual/corrected authority still wins. Current-cost fallback is separately labeled `current_bundle_catalog_estimate`, `cost_stage=expected`, `historical_cost_verified=false`; never counted twice.
- `backend/dashboard_v2_routes.py`: both standard and cooperative product-cost builders use the same estimated fallback. `totals.total_product_cost`, `product_cost_v2.total`, product rows and executive profit therefore share one cost per selected order line and the existing refund/cancellation scale once.
- `backend/sold_products_report_v2.py`: same estimate in sold-products report.
- `backend/tests/test_dashboard_bundle_cost_authority.py`: unpriced four rose units = 220; rose+name four units = 260; change to current SAR16+45+11 = 288 for same four units, while original frozen costs remain unchanged; mixed choices, conflicting yes/no, missing selections, order-row/catalog ID differences, report read bounds and zero operational writes.
- Explicitly unsupported advanced or ambiguous recipes stay incomplete rather than fabricate a complete profit.
- Former owner-attested single-order correction plan/apply APIs remain disabled by default; they are **not required** to display a current-cost estimate. No Production DB writes, financial posting or backfill.

**Current checks:** Exact-HEAD GitHub CI for source `6bfff5c1...` is queued (G47, Dashboard runtime adoption, Security, CodeQL, etc.). Do not claim green acceptance until results are inspected. Existing earlier green source-A checks apply to the previous historic-correction scope only.

**Next safe action:** Review exact SHA GitHub runs, repair any regressions and validate Product Card versus Executive Profit and Sold Products report, then prepare reviewed v5 A/B intent. Keep Production deployment and all data writes separate, explicit actions. No Production change in this milestone.
