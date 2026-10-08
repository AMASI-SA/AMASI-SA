# Backend performance PR1 — A + D

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
