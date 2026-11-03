# MZ2-CURRENT-BUNDLE-COST-REPORTING-1706 — checkpoint (2026-11-03)

Issue: #1706. Draft PR: #1708.
Repository: AMASI-SA/AMASI-SA.
Task branch: `codex/mz2-bundle-historical-reporting-1706-20261103`.
Source Production base J: `e01d4d3920fc5bf4a7280757ca2030c30b4be9c8`.

## Final owner policy for this task

Any **currently configured Mezan unit/component/service cost** is counted on **all sold product units in profitability reports**, including pre-existing sales, conditional on each order's selected options. The owner confirmed for SKU AMS13281: base shawl 15 SAR + rose 40 SAR if selected + external supplier embroidery 10 SAR if name is selected. One unit with both selected =65; four units with both=260. Later current prices (16+45+11)=72 each and four units=288. Do not assume every sale has rose+name choices.

This is *reporting only*: no posting invoices, suppliers, receipts, stock reservations, journal entries, Qoyod or mutation of orders. The product card, Executive Profit and Sold Products reports use the same shared cost authority, so same filters/period/return policy apply without double counting. Fully recorded frozen/actual/approved costs stay authoritative; the current-cost estimate is only for a composite order line with **no stored cost authority**, and is explicitly labeled `current_bundle_catalog_estimate`, `historical_cost_verified=false`, `cost_stage=expected`.

## Verified user observation from Production

- On 2026-11-03, order 297657209 SKU AMS13281 had quantity=1, `snapshot_exists=false`, `cost_authority=unresolved_snapshot`, `missing_reasons=["bundle_snapshot_missing"]`, cost null.
- Nov 2-3 dashboard: AMS13281 15 sold units shown with missing cost, 46 orders with incomplete costs; Executive and Product V2 currently both show 18,353 SAR known cost but exclude unpriced bundle sales. This is the unrepaired Production baseline.
- Source Salla option row IDs do not necessarily match their catalog option IDs. The financial estimator checks actual order option/value IDs or unique exact labels, never rewrites the provider selection or supplier execution context. All ambiguous/missing/stale recipe choices fail closed.

## Current code milestone (not a deployable v5 release yet)

- `backend/current_bundle_reporting_estimate.py` (NEW): read-only per-selected-line current bundle cost projections. Tenant-scoped batch collection reads, original order quantities, typed base/product/physical components/independent external service SAR costs. No per-order Mongo queries, limited to missing composite lines; honors immutable cost authority precedence.
- `backend/order_cost_authority.py` (MODIFIED): computes those estimates only after reading original snapshots, shares one result with existing Dashboard standard/cooperative and Sold Products readers. Existing correction and frozen cost logic unchanged.
- `backend/tests/test_dashboard_bundle_cost_authority.py` (MODIFIED): selected rose/name service matrix, current cost revision for all sold units, privacy-safe real-shaped Salla per-order row IDs + text, no cost on missing/conflicting IDs/conditions/duplicate line IDs, exact snapshot precedence, batched read budget, no operational writes.
- `backend/dashboard_v2_routes.py`, `backend/sold_products_report_v2.py`, `backend/order_option_cost_snapshot_routes.py`, `.github/workflows/g47-focused-integration.yml` are restored **byte-identical** to protected Production source. This prevents bypassing immutable source/response/CPU budget acceptance.
- An earlier owner-attested historical per-order correction/write path in this Draft PR was **removed** after policy clarification; no historical cost apply endpoint or DB mutation is proposed by this task. The current-cost read path is sufficient.

## CI and gates

- Source `d45637ad8880e58dc9a695131e6087d61d04ced2`: G47 327 tests +127 subtests PASS and 466 cost/report tests PASS, but Dashboard runtime adoption FAILED because protected report source files had changed. **DO NOT DEPLOY THAT SOURCE**.
- The source-pinned report functions were then restored and cost logic centralized in the authority, avoiding changes to the protected Dashboard reducer and Sold Products files.
- Current implementation checkpoint before this doc commit: `b50afe2b7fc785157fb915eb0243f48dfb8bc71b` (code changes relative to J only). Exact-head G47, Dashboard runtime adoption, security, CodeQL, stock and release checks **must be confirmed SUCCESS** on a stable final source head after all reviews. No current-HEAD PASS claim until verification.

## Exact next action

1. Inspect newly triggered CI for this documentation checkpoint; fix only the task branch if any failing gate, and repeat exact-head checks.
2. Require G47 real Mongo tests (unpriced bundles, mixed choices, no supplier/GL writes) plus Dashboard runtime adoption's immutable 164-response oracle and read/CPU budgets; CodeQL, Release Readiness, Operational Stock Eligibility, Security.
3. When all green, prepare v5 reviewed A/B intent via sanctioned Emergent local host and review candidate B. A clean merge into Production and actual Emergent publish remain separate approval-gated steps; no source-only merge into Production.
4. Post-publish compare authentic Production current cost on exact order 297657209, one selected unit expected 65 SAR if current configured choice matching is verified; verify SKU AMS13281 all 15 selected units individually, executive cost vs product card parity, and no accounting/stock change.

**Production code changed by PR #1708: NO. Production data written by this task: NO.**
