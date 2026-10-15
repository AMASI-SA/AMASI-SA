# Cross-task execution eligibility

Validated source: `ee95c9fcee9d1d4db69d6fdedb7be22e6e5c4f97`.
Base: exact Review #1303 `4fd83818fb14c0bafde922b97d544d44b05e30d0`.
Shipping reference: `d187dfbb729923f491f6ce3a106dde043e2b890c`.
Production reference: `f50977c13adf1743e66d11f6e6146527f6e25018`.

## Scope and ancestry

This independent branch changes neither original PR head. Review base descends from Production source c411e6b, with #1302 already present; f50977c's later Intent-only commit was not merged or copied. This is not a release candidate or a full replacement of #1305. Its UI/status print gate is intentionally not ported: only the shared execution policy and internal completion guard are in scope. The existing store_courier_completion_attempted claim from #1305 is reused verbatim, with no additional field, canonical lock, owner expansion or provider client change. No release or Production action occurred.

## Actual policy

| Contract / external state | Stage / piece | Result |
|---|---|---|
| Proven Local v1, recognised review/pending/in-progress state | reviewed, eligible virtual/direct unit | Can start; existing transition to local in_progress |
| Proven Local v1, same external envelope | in_progress / ready_to_ship, eligible physical or virtual unit | Can continue, existing custody/component guards retained |
| Missing/invalid Local proof or unknown contract | any | Denied, no legacy fallback |
| Either contract, shipped/delivered/unknown/missing status | any | Search remains readable, mark-ready denied before business writes |
| Historical provider-backed, recognised execution state | original stage requirements | Original physical/virtual stage semantics retained |
| Partial receiving / missing unassigned unit | in_progress | Full-coverage predicates unchanged; not ready_to_ship |
| All preparation units covered | current preparation lifecycle | ready_to_ship according to existing coverage predicate |
| All assembly units ready | completed | Existing completion callback; internal courier verifies provider completed via readback |

## Fresh tests

Windows local Python, real MongoDB 8.0.12, replica set shippingfulfillment, writable PRIMARY, loopback only. Salla transport is synthetic; no Production/customer request was made.

| Suite | PASS | FAIL | SKIP |
|---|---:|---:|---:|
| Cross-contract (reruns current mixed/receiving scenarios; includes one pure policy test) |30|0|0|
| Local Review regression |38|0|0|
| G47 real-Mongo integration |19|0|0|
| Preparation regression |55|0|0|
| Shipping compatibility (memory + real Mongo) |214|0|1|
| Total |356|0|1|

The Shipping real-Mongo parameter cases separately are 77 PASS / 0 FAIL / 0 SKIP. The sole skip is test_real_transaction_failure_leaves_no_partial_label[memory]; its real Mongo case passes. It is not counted as a pass.

Command shape: `python -B -m pytest --noconftest -p no:cacheprovider --asyncio-mode=auto -q --tb=short <suite files> --junitxml=<evidence>` with loopback MZ2_TEST_MONGO_URI and BUILD37_TEST_MONGO_URI. Raw XML/logs and first-run failures are retained outside the checkout in C:/Users/amasi/cross-task-execution-evidence. STATUS.json records suite counts and timing.

## Evidence mapped to requirements

- Real Local Review -> pending external state -> direct start -> local in_progress -> all units complete: inherited test_real_local_review_direct_assembly_starts_then_completes, rerun in CrossTaskExecutionTests.
- Supplier units remain assignable: test_mixed_unassigned_units_survive_direct_start_and_supplier_assignment and mixed mobile/concurrent assignment cases.
- Partial receipt: test_partial_receipt_preserves_mixed_remaining_units_then_completes, test_direct_finished_before_partial_supplier_receipts, test_supplier_only_partial_quantity_receipt_and_duplicate.
- Custody/receipt concurrency and rollback: test_concurrent_duplicate_receipt_commits_custody_and_event_once and test_receipt_transaction_failure_rolls_back_custody_and_progress.
- Both contracts + physical/virtual + shipped/delivered/unknown/missing: test_status_rejection_both_contracts_search_endpoint_zero_writes checks every persisted document before and after search and direct endpoint calls. Existing empty collection/index bootstrap is excluded, not business documents.
- Duplicate/concurrent mark-ready: test_duplicate_concurrent_virtual_consumes_each_unit_once plus G47 physical consumption/rollback tests.
- Final local assembly -> existing provider transition -> confirmed readback: test_final_assembly_internal_completion_uses_real_transition_readback, with the completion callback isolated from unrelated issuance; actual helper and provider status/readback logic are exercised using synthetic transport.
- Uncertain readback / concurrent completion: test_completion_concurrent_attempt_and_uncertain_readback_do_not_repeat_post.
- Legacy historical stage semantics: pure policy test and G47 provider-backed fixtures, with explicit canonical status/date rather than absent source rows.
- Current Shipping print path and selection: unchanged AST against Production, plus fresh 214-test Shipping suite. This patch does not introduce a new print eligibility policy.

## Limits and rollback

Snapshot policy does not serialize with external canonical-status writers. A status change after the transaction snapshot remains an explicitly unresolved race. No new lock/revision/transaction architecture was introduced. Source completeness and provider status failures deny completion; provider transport failures remain visible.

Rollback is to exclude/revert this independent policy delta while preserving #1303's supplier/coverage changes and #1302's print changes. There is no migration or backfill. Do not replace shared files wholesale.
