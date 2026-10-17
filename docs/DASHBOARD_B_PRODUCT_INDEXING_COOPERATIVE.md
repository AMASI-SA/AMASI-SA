# Product indexing cooperative experiment (not runtime)

Approved scope: Draft PR #1301, after the indexing RCA at
`5b049360295688df8e0b2784fbfb5c60c5ddfa3b`. Current synchronous indexing versus
1/5/10ms CPU-budget checkpoints. Parser5, cost5 and advertising1 are fixed in
all four arms. Only private test compilation changes; no production module is
modified or imports these helpers.

## Contract

Compile the original index function, inserting a cooperative checkpoint before
each complete product and then before each complete name group. One accumulator
and budget span both ordered passes. Finish aliases only after all products.
Keep ID/SKU last-wins, alias first-object semantics, shallow references, variant
reconciliation, exception behavior and every original calculation unchanged.
No sleeps, batch merges, rounding changes or reordering.

The Current arm awaits a wrapper that contains no await: its original index
call remains synchronous. Active slice wall/CPU is separate from inclusive
async duration; remove the old enclosing synchronous indexing timer in the
private compiled cost builder so it cannot misattribute other coroutines.

The budget is a scheduling target, not an upper latency bound. One product with
many variants, one name group, GC and function cleanup can exceed it. Retained
index objects are required for existing reference semantics.

## Gates and measurement

Correctness first: ordered JSON, reference graph, alias collisions and ambiguity,
duplicate/raw variants, malformed inputs, cancellation, private accumulators,
mixed full Dashboard filters/currencies/permissions/tenants and prior independent
read mutation schedules. New checkpoints occur before later cost reads: test
actual writes during yielding and preserve the original independent read points.
No fixed-input-only equivalence claim; record limits of controlled schedules.

Linux/Python3.11/MongoDB8.0.12 isolated replica set, independent OS-process HTTP
client, one warmup and eight measured waves. Four-arm Williams ordering balances
positions and preceding treatments. Cases:10K multi-same,100K single-same,100K
multi-different. Same existing governor capacity2 and Dashboard weight2. Samples
are small (8 single/24 multi Dashboard observations); p99 is a diagnostic tail,
not a Production SLO estimate. Report per-endpoint interactive tails as well as
aggregate tails, CPU, throughput, Mongo, heartbeat, serialization, RSS and errors.
Do not sum overlapping phase measurements. Compare exact response hashes and
Dashboard Mongo command counts every wave. A diagnostic materiality screen for
interactive tails is >10% AND >25ms, with paired waves and endpoint results also
examined; Dashboard/throughput regressions must be disclosed separately.

No runtime adoption, shared DB reads, cross-request cache, transaction/snapshot,
Governor change, process/worker, Accounting, Shipping, Review or Android change.
No merge, prepare, deploy or Production request. Production writes=0.

Exact-head CI artifacts and final decision are recorded in the canonical Issue
#1006 ledger and linked from PR #1301 after the run. Pending until measured;
neither this plan nor syntax checks claim a performance win. Rollback consists
of removing these test/workflow/doc additions; runtime is untouched.
