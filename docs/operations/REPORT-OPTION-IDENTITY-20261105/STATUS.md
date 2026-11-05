# REPORT-OPTION-IDENTITY-20261105

Scope: selected-option costs in best-selling products and executive profitability; no financial postings, supplier-price changes, stock changes, production writes or deployment.

## Evidence and changes

A synthetic reproduction returned 573.60 instead of 677.60 for 28 units at 15 base + 1.20 bag + 8 selected embroidery. Fifteen Arabic choices matched; four translated choices and nine checkout-row-ID choices lost their 8 cost. This reproduces the numerical symptom, but is not proof that production's nine lines have that exact raw shape. Production source payload/snapshot classification remains a rollout check.

Shared report catalog costs now resolve explicit provider option/value IDs first, distinguish checkout record IDs from catalog IDs, and recover legacy labels only via a unique option-scoped catalog/binding identity. Provider translations and retained historical labels are catalog metadata; there is no fuzzy matching or global Yes-to-service inference. Unresolved cost-bearing choices become incomplete rather than silently free. Cost snapshots retain resolved IDs. Existing financial snapshots/fingerprints are not rewritten.

Catalog detail refresh retains observed labels/translations under the same IDs and invalidates report caches when options change. Both dashboard reducers and sold-products projections load the required catalog fields. Composite estimate recovery reads the same identity aliases. Missing resource cost is returned as null and displayed as unrecorded, while explicit zero remains valid.

## Validation

From backend, with MEZAN_CGROUP_ROOT=/nonexistent-mezan-test-cgroup (isolate tests from the shared host's cgroup memory admission):

`python -m pytest tests/test_report_option_identity.py tests/test_dashboard_v2_product_cost.py tests/test_order_cost_authority.py tests/test_order_cost_identity_projection.py tests/test_order_cost_financial_readers.py tests/test_product_option_shared_cost_engine.py tests/test_dashboard_bundle_cost_authority.py tests/test_sold_products_report_v2.py tests/test_product_field_cost_support.py tests/test_order_cost_final_review.py tests/test_product_v2_full_details.py tests/test_product_option_price_evidence.py tests/test_bundle_reporting_cost_policy.py tests/test_bundle_reporting_financial.py tests/test_qoyod_verified_unsent_summary.py -q`

239 passed, 4 skipped (existing real-Mongo integration gates); two existing Pydantic deprecation warnings. Regression failed before the implementation (5 failures, including 573.60 vs 677.60). Read-only integration verifies 21 orders/28 independently identified lines across both dashboard reducers and sold products, total 677.60. Existing frozen/actual cost priority and supplier behavior tests pass. JSX parses with Babel; a full governed frontend build has not run.

## State / next steps

Dedicated branch: codex/report-option-cost-integrity-20261105. Original source baseline d80661fd27ef88bf639fa43d8f969f4d62239a61; integrated latest production source a67da7816f24befb09fb713b294dea70e9517100 without conflicts, preserving Qoyod changes. Draft checkpoint, not deployment-ready evidence. Exact pushed commit recorded in Draft PR #1763 (issue #1006 rejected comments at GitHub's 2500-comment limit).

Next: review CI and diff, perform governed frontend build before release, inspect real affected rows for existing frozen incorrect snapshots (requires explicit versioned correction, never silently overwrite), then separately authorized release and live verification of both report surfaces. No production data, merge, backfill, or release guard lease changed. Do not claim the production discrepancy is resolved before those checks.
