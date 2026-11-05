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

## Follow-up: explicit unresolved-cost contract

Owner confirmed that every unresolved cost-bearing choice must remain incomplete. Missing optional answers, unknown explicit values in a known catalog option, and unmatched legacy bindings now return unresolved rather than unselected. The executive API returns `product_cost_complete=false` and null net profit (and null cost-dependent net sales). The UI displays `ناقص التكلفة`, explains pending choices/costs, and suppresses stale profit and margin even if a prior response contains a number. Confirmed selections of a known non-cost value remain valid; explicit zero resource cost remains valid.

Validation: 244 backend tests passed, 4 existing real-Mongo skips; 39 AdvancedDashboard UI tests passed, including stale numeric profit suppression. Four focused regressions failed before the follow-up fix. Full governed build rehearsal is next; production remains unchanged.

Known Salla fallback cost remains distinct from unresolved cost: the final completeness flag checks unavailable cost components and orders without products, not merely missing Mezan-specific pricing. A dedicated HTTP regression verifies known fallback profit stays numeric. Latest focused check: 29 report integration tests and 39 UI tests passed. Full A/B frontend rehearsal on d4956e8f60fdac0b105d6eb4b02e299e13aaaaf8 passed; repeat on final source is required because the fallback-completeness adjustment changes the UI. Latest exact build outcome is recorded in PR #1763 to avoid rewriting source after the proof.


## Release gate contract follow-up

The first frozen pair A=63c8ff45fef66cf83fa1810593dc0b5f7c86b93c / B=07e5171e4ed353881967aa8c755c216060bc5e97 passed local adapter rehearsal and both Emergent host CI rehearsals. The historical dashboard response gate failed because totals.product_cost_complete is an intentional owner-requested API extension. A new source/intent pair is required; no merge or production publish occurred.

The gate now independently derives the exact permitted flag and profit/net-sales suppression from the immutable reference response's missing-cost counts, preserving all other JSON values/member order and all Mongo command/tenant/document checks. Six negative-test scenarios reject wrong flags, leaked profit, altered sales and altered net sales. The sold-products source pin adds only the reviewed catalog options projection hash. Local gate/source tests: 49 passed plus 5 subtests. Local real-Mongo attempt could not start (open: Operation not permitted); full real-Mongo acceptance remains required in GitHub CI. Existing 245 backend / 39 UI results and first-pair build evidence remain recorded in PR #1763; new pair needs fresh build and CI. Production unchanged; no lease opened.


## Current production integration after PR1770

Production advanced to 7032e0f93ac22c9de83af0d80d3b0a1e3fb2c852 while another authorized release owned the shared lease. All task changes apply cleanly without modifying the Qoyod update. Prior exact pair A33acde8e/B7d49803e is preserved in PR1768:39 CI checks completed (33 success,6 scope skips), including both host20/24 clean-clone adapters and frozen real-Mongo gate; local adapter PASS. That pair must not be deployed atop the new production tree. Prepare a fresh source/intent pair on 7032e0f9, then a fresh PR with this current base to avoid the stale PR-base event seen in1763. No product/service costs or financial snapshots changed. No merge/publish by this task yet. Shared lease last observed belongs to qoyod-1770; do not clear it or change origin production while it remains active. Exact final SHAs and final deployment evidence are maintained in the continuation PR.
