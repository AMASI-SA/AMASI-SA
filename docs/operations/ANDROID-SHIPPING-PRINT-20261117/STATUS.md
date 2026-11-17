# Android shipping-label read parity — 17 November 2026

## Scope and source

The owner reports that shipping-label download/printing fails across Android
orders while Mezan web can print the same completed orders. This server change
repairs the native explicit-fetch route; the independent embedded-WebView
message-origin repair belongs to `AMASI-SA/amasi-mobile`.

Repository: `AMASI-SA/AMASI-SA`.
Task branch: `codex/android-shipping-print-parity-20261117`.
Reviewed base: `d7c9ed77137bed0948f4d32a34c6bf571d75e6f8`.

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

The owner requested the repair. No new production merge/publication or
Android distribution has been performed by this task. A production release
requires the repository's separately authorized protocol-v5 sequence and
fresh checks against the then-current base/lease; do not reuse an earlier
release intent or another task's lease. A physical Android/PDF/printer check
remains required after the server and app fixes are delivered.

Production changed by this task: **no**.
