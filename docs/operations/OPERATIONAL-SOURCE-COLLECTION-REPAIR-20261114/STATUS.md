# Operational source-collection repair — source work in progress

## Problem and verified base

Production base J is `9c71435f4a19977c09f4331dee8fddbfa12a45eb`, tree
`c5bd7fb6c357e17c17104282d1d175676e95ed3e`. PR1916 was merged, published as
platform `bfa6a5f`, and passed official identity verification. The visible
financial report still retained its previous snapshot.

A controlled visible attempt begun2026-11-14T03:57:55.990Z completed
`native_evidence` in2926.310ms and `snapshot_load` in9084.282ms. Then
`source_collection` was cancelled after72942.769ms by the85040.132ms total
request deadline, returning408. Reconciliation did not begin. Exactly three
financial attempts occurred on PR1916; no further attempt is planned before a
source change. These timings localize the remaining failure to collection,
but do not identify a particular subphase or justify attributing all of its
time to the optimization below.

## Focused change under construction

The source collector calls `recorded_courier_fee` for each eligible order.
Without a direct saved fee, that helper repeatedly scans the complete facts,
obligations, movements and allocations. Separate instrumented samples recorded
4096 visits per history category at64 lookups over64 rows, and16384 at128 by128:
four times the work for twice the input size.

Uninstrumented synthetic measurements at the exact production source recorded
2.375s wall/2.135s CPU for1000 lookups over1000 rows;20.167s wall/18.787s CPU for
4000 by4000; and11.017s wall/10.376s CPU for250 lookups over20000 rows. A control
with direct saved fee evidence performed20000 lookups over20000 rows in0.418s
CPU. All completed with actual exit0. These are offline synthetic observations,
not production latency improvements or a claim of full resolution.

The intended fix is a request-local fee-history index, built once from the
complete immutable attempt state. Legacy malformed structures retain the
existing behavior; no eager validation of unrelated financial values is
permitted. The existing unindexed helper remains the compatibility reference.
The owned worker must remain cancellable and joined, with a new index for each
collector invocation and CAS retry.

The same patch adds fixed-name `refresh_stage` wrappers around source
collision, primary reads, secondary helpers, order/invoice conversion, native
overlaps and finalization. The existing timing mechanism emits the exact
cancelled subphase during unwind without new I/O or private business data.

## Safety and scope

Complete owner data, source contradictions, historical records, row caps,
authorization, CAS and the85-second request deadline remain required. No
assignment/cost/reconciliation optimization is included. No financial
confirmation, payout, settlement or provider write is used as a test.
Original `/app`, its120 unrelated untracked files and unsaved `Untitled-1`
remain untouched by this follow-up; no release lease has been created.

## Verification and release state

Implementation, parity/error-order/cancellation tests and independent review
are in progress. No source A or intent B is frozen. Nothing from this
follow-up has been merged or published. Source branch:
`codex/operational-source-collection-fix-20261114`.

Documentation-only checkpoint branch:
`codex/operational-source-collection-handoff-20261114`.
Issue1006 reached its2500-comment limit; its final entry documents continued
tracking in task PRs. The handoff branch and forthcoming task PR preserve
verified milestones without changing frozen source commits.

Next: finish implementation, prove whole-collector/reconciliation parity and
bounded indexing work, run independent review and focused regressions, then
freeze all source/tests/documentation as A. Require exact-A CI and reviewed
artifact, intent-only B, normal merge, actual original build, prepare/prepublish,
one publish, official verification and a fresh visible financial as_of before
claiming the user's issue resolved.
