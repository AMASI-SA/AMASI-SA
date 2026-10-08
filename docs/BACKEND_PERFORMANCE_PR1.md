# Backend performance PR1 — A + D

## A verified — user acceptance recorded

A implementation is closed after explicit review acceptance. No further A
optimization is planned. PR1294 remains Draft, not merged or deployed.

Evidence lineage:

1. Original serial baseline: `c203cbcb53cb0015f6baec47f9ee681445525d52`.
2. Retained batch500 implementation: `8d87df8a55a42b7e0dcb0e2048c15729243b94e4`.
3. Approved bounded-retention experiment: `648e944939157e2efe2120e4d616cc12a13e49ee`.
4. Actual runtime adoption: `5c67778926bbbfd86717a976ffb0bc95a7064fdb`, tree
   `c8b9e7abd37f16d0f6ca0e551fe6592c89b1b81b`.

Exact runtime CI: https://github.com/AMASI-SA/AMASI-SA/actions/runs/37831369738
Raw evidence: https://github.com/AMASI-SA/AMASI-SA/actions/runs/37831369738/artifacts/11573722787
157 tests +10 subtests PASS; zero failures/skips. Linux/Python3.11, isolated
MongoDB8.0.12 replica set. Thirty samples per implementation and dataset.

| Workflows | Commands | Runtime p50/p95 ms | Runtime CPU p50 ms | Peak DTOs | GC p50 ms |
|---|---|---|---|---|---|
|50|4|25.26 /27.04|21.86|45|0.260|
|500|7|237.70 /284.81|224.86|466|5.593|
|5000|25|2528.44 /2623.07|2424.30|468|226.39|

Original serial5000 commands were5005; batched/runtime commands remain25.
The retained-batch design held4671 DTOs versus468 in both experiment/runtime:
approximately90% fewer peak live DTOs. This retention comparison is against the
original batched design, not the serial baseline (which processed single orders).
Same-run experiment/runtime5000: p502524.72/2528.44, p952660.17/2623.07,
CPU2402.38/2424.30, GC212.51/226.39ms. At50, p95 increased0.535ms. These small
differences are explicitly preserved and accepted by the user as no material
performance regression after adoption, not hidden or asserted to be statistically
zero. No production latency or RSS-byte reduction is claimed.

Exact JSON/total/order/filter/pagination/duplicate/malformed equivalence and
permission/regression tests pass. Runtime AST matches the approved experiment.
Rollback adoption alone: revert commit5c677789 to restore retained batch500 behavior.
Rollback all A: revert only the preparation handler changes to the serial baseline;
keep D separate if desired. Neither rollback needs data/index/schema migration,
and neither authorizes production deployment. Production writes=0.

B (Dashboard) is a separate phase/branch/PR, beginning with RCA and measurement.

## Authorized runtime adoption

The approved experiment is now installed in `_assembly_order_board`: fetch at
most 500 identities, construct display rows, release that batch's DTO dictionary,
then fetch the next batch. Original workflow indices restore stable tie ordering
before the unchanged final sort and pagination. All eligibility/display predicates
and duplicate/malformed handling are preserved.

`test_board_stream_experiment.py` proves structural AST equality with the approved
experiment and compares the actual runtime callable (not a reconstructed runtime)
against the experiment and prior retained implementation. Linux CI uses the same
MongoDB 8.0.12 replica-set fixture, 30 samples per size, all six implementation
permutations balanced, exact JSON comparisons and unchanged collection hashes.
Runtime metrics include Mongo commands, p50/p95, CPU, GC and weak-reference live
DTO counts. The runtime call is not modified for display/sort instrumentation.
The final exact-head CI artifact is the acceptance evidence; test success alone
does not establish absence of a performance regression. No release-gate changes.

Base: `c203cbcb53cb0015f6baec47f9ee681445525d52`.

Scope is bounded canonical-order batch reads in `_assembly_order_board` and
dashboard admission/execution timing. No business predicates, sorting, totals,
pagination, response fields, governor capacities, weights or timeouts change.
No production access, deployment or production writes are part of validation.

## A: preserve board results

Use existing `get_orders` with batches of 500 candidate order identities. A board
repository adapter remembers the first raw identity before DTO conversion,
including malformed first records, and ignores later duplicates in the same
cursor. This avoids a separate duplicate-probe/read race while reusing the
existing repository query, projection and mapping. Canonical reads become one
logical read per batch rather than one per workflow; cursor `getMore` commands
are reported separately. Duplicate selection retains the existing unsorted
read contract; no new sorting rule is imposed on ambiguous duplicate data.
Physical-piece and workflow reads/limits remain unchanged. All rows are still
mapped and sorted before the existing pagination; this does not hide data.

Real-Mongo tests load the old function directly from the immutable base commit.
Fixtures exercise both states, query, offset/limit, missing/malformed records,
duplicate identities, virtual pieces, permissions and tenant isolation.
Measured fixtures use 50, 500 and 5000 workflows with one warmup and ten samples
per implementation. p50 is median; p95 is nearest-rank. Timings measure the
async board handler, excluding network and authentication; ASGI tests separately
verify the endpoint contract. These are local synthetic measurements, not a
production speedup claim. Thread CPU mapping measurements have platform clock
resolution limitations. No latency threshold is used as a correctness gate.

## D: timing contract

The existing resource-stage log now additionally carries `admission_wait_ms`,
`execution_ms` and `total_ms`. Execution starts after admission and includes
governor cleanup; blocked/cancelled waiters have zero execution. Existing
`duration_ms`, statuses, exceptions and HTTP 503 responses remain unchanged.
Deterministic tests cover success, pressure, errors and cancellation, including
an actual task cancelled while awaiting admission.

## Validation and known baseline failure

The focused CI runs the immutable-head checkout against isolated MongoDB 8.0.12
replica set `performancepr1`; artifacts contain timing/query evidence and JUnit.
Missing Mongo causes a skip locally but CI rejects any skipped test.

An exploratory broader run returned **152 passed, 1 failed, 0 skipped**. The
failure is the pre-existing frontend source assertion in
`test_dashboard_v2_live_refresh_contract.py::test_advanced_dashboard_refreshes_summary_from_live_orders_without_fake_zeros`
requiring `setData(null)`. Both that test and `AdvancedDashboard.jsx` are exactly
unchanged from the base, whose source lacks that string. It is outside A+D and
is not included in the new focused CI suite; it has not been fixed or marked
xfail. Existing CI definitions are unchanged. This known failure must remain
visible during review.

The repository's separate Mezan Release Readiness workflow also fails at source
classification: `frontend source differs from reviewed intent:
src/components/fulfillment/CompletedFulfillmentOrders.jsx`, followed by
`production base contains unreviewed non-documentation changes`. Those frontend,
release-intent and adapter files are unchanged by this PR. No release intent or
release gate is modified to bypass it. This is a review blocker outside A+D,
not a successful release-readiness result.

## Rollback and limits

Reverting this standalone patch restores per-order reads and prior logging.
No schema, data migration, index or API contract changes are required. Batching
reduces database round trips, not the number of mapped candidate orders.
Reads retain existing non-transactional consistency; this does not introduce a snapshot
guarantee. Dashboard computation, Ready UI, background isolation, product filters,
Accounting, Review Completion, Shipping/Printing, Salla and Android are untouched.
