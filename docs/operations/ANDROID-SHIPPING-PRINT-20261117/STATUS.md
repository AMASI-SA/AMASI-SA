# Android shipping-label read parity — 17 November 2026

## Scope and source

The owner reports that shipping-label download/printing fails across Android
orders while Mezan web can print the same completed orders. This server change
repairs the native explicit-fetch route; the independent embedded-WebView
message-origin repair belongs to `AMASI-SA/amasi-mobile`.

Repository: `AMASI-SA/AMASI-SA`.
Release branch: `codex/android-shipping-print-release-20261118`.
Reviewed production base: `b92d3eb8d12920663a052744c9c0d2773a785e1a`.
The original verified source remains recoverable as
`00e8e0c5d97ae71bfd119b95f61633dda2378bdd` on
`codex/android-shipping-print-parity-20261117` / PR1973.

## Reproduced problem and change

The Android `carrier-label/v1/complete-and-refresh` route previously used the
ordinary current-label read first only for merchant couriers. Every external
carrier instead entered manual completion, including already completed
orders. That unnecessarily required the additional local evidence intended
for a status write and could issue a redundant completed-status POST.

The actual-route regression demonstrates two failures before the fix: an
already completed external order sends an unnecessary status POST, and a
durably completed order with incomplete cached raw items returns the reported
completion-required message even though the normal current-label read finds
the correct current provider PDF. The incomplete cache is a synthetic
distinguishing fixture, not a claim about a particular live order.

All carriers now attempt the existing current-label read first. Only a fresh,
explicit `order_status_completed: false` response enters the unchanged fenced
completion operation. Provider failure, cancellation, unknown status,
permission revocation and changed shipping cannot select that fallback.
The merchant-courier completed-stage restriction remains unchanged.

No background job, polling loop, shipment-creation API, automatic retry,
order-specific exception or direct data repair is added. The read retains
the existing durable completion, current-provider, ownership, permission and
shipping snapshot checks. It does not impose manual status-write evidence
on an already completed order.

## Verification and continuation

Focused RED evidence: both new completed-external cases failed for the
expected reasons on the base route. Focused GREEN: 19 route tests passed,
with no skips, after the change. The final bounded five-suite run was repeated
independently by the parent: 145 tests and 59 subtests passed, no skips or
failures, exit 0. The pre-existing dispatch-only test was updated deliberately
to require the fresh unconfirmed read before manual completion. Cases cover repeat reads without provider
writes, current unconfirmed completion, incomplete evidence, nonprintable
statuses, provider errors, employee grants, revocation and shipping races. A focused provider-timeout regression also failed before the
read was moved inside the existing protected endpoint boundary; the final
route returns the existing structured 502 response without a status POST or
workflow change.

The new suite is included in the existing Review Completion Resumable CI
path trigger and Android-operation run list. The exact remote source commit,
final broader local checks and CI results are recorded in this task's draft
pull request. Issue 1006 has reached its 2,500-comment limit; use this status
file and that pull request as the continuation record.

On 18 November 2026 (Asia/Riyadh), the owner explicitly authorized publishing
Mezan first, with Android distribution afterward. That authorization permits
this named server repair's normal reviewed merge and protocol-v5 publication;
no repeat permission request is required. The Android PR360 remains separate
and unreleased for the later app step.

All applicable original-source checks passed: 27 successful check-runs,
6 expected scope skips, zero pending/failures. Independent exact-source CI XML
inspection confirmed all 19 new cases executed within the Android operation
suite (348 cases plus 59 subtests, zero skipped/failed/errored cases).

Production advanced through PR1972 to b92d3eb while the original source was
under review. Its 20 changed paths do not overlap this repair's five paths.
The successor source is constructed directly on that newer production commit,
preserving its Qoyod, frontend, security, courier and tracked intent bytes.
The original source is not merged across an intervening intent history.
A new candidate intent and exact final-B gates are required for this source.

At this checkpoint, PR1972's owner is still executing the original shared
workspace adapter/publication and holds a preview backend restoration
obligation. Do not mutate that workspace merely because its pre-lease status
is inactive. Wait for the owner's complete release/handoff, then independently
require the official inactive guard before this task's actual adapter and
lease. No earlier release intent or another task's lease may be reused.

No production merge/publication or Android distribution has been performed
by this task yet. A physical Android/PDF/printer check remains required after
the server and app fixes are delivered. Exact source/intent SHAs, CI, merge,
guard and deployment evidence will be recorded in the successor PR body,
without changing governed source after the intent is frozen.

Production changed by this task: **no**.
