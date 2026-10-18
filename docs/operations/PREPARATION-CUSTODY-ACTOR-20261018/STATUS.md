# Preparation receiving custody actor and current Salla status

## Scope and status

Task branch: `codex/preparation-custody-actor-20261018`.
Base: `origin/hotfix/prod-snap-meta-final` at
`e015b913ce950d6e5cf52cc0f1893da94b7b30b4` (fresh fetch on 2026-10-18).
Isolated worktree: `D:/amasi-build/backend-preparation-custody-20261018`.

The owner reports only the owner's preparation card appearing in Android's
preparation receiving custody, while pieces received from other preparers are
missing before assembly. The owner subsequently supplied one example. ADB
read-only UI inspection of installed APK47/1.0.32 confirmed a non-owner preparer
and "already received" for that piece, while the custody list showed only the
owner's card. The sidebar confirmed a signed-in receiving employee. Raw UI
evidence remains local; no customer/order data is committed here. The UI does
not expose the persisted receiving-actor ID or assembly state, so those fields
were not read directly and no historical repair is claimed.

## Confirmed mechanism and change

The native request adapter validates the employee's live page grant, then uses
a merchant-shaped principal while preserving the authenticated employee in
server-owned `_mobile_actor_*` fields. The receipt POST already persists
`effective_operation_actor(user, context).id`. The custody GET instead queried
`context.actor_id`, which is the owner under that native adapter. A receiving
employee therefore saw the owner's receipts and could not see their own.

The custody GET now resolves the same effective receiver as the receipt POST.
Source-preparer grouping includes both owner and non-owner preparers. Tenant
scope, own-receiver scope, permission checks, source-employee snapshots and
receipt idempotency remain unchanged.
There is no history rewrite, data migration, Android change or provider call.

## Owner-approved visibility rule

The owner subsequently requested that receiving custody show pieces only while
their current Salla order is `in_progress`, including status changes made
outside Android. A stale local `ready_for_assembly` piece must not keep a
completed/shipped order in the queue.

The custody read now fetches the relevant tenant-scoped `unified_orders` status
fields once and filters pieces before source-employee grouping and totals.
Empty employee cards therefore disappear. Order-number and barcode search in
the receiving page use the same filter and return an Arabic explanation when
the order is not eligible. The installed Android error helper already renders
this server message; no APK change is required for these reads.

Only unambiguous provider/root `in_progress` status qualifies. Missing,
contradictory or explicitly stale status evidence is excluded. A component-only
reconciliation phase does not hide an otherwise eligible order; local workflow
stage does not override Salla status. Receipt records and events are untouched,
so reopening an order in Salla can reveal its original receipt again.

Freshness boundary: this uses the latest **synced** Salla status in Mezan, not a
new provider call per piece. External changes take effect after synchronization
and the next custody/search read (page refresh/reopen). It does not introduce
live push/polling, change the separate local session history, or alter receipt
write, assembly, shipping, stock or financial state transitions.

## Evidence

Real loopback Mongo 8.0.12, disposable synthetic database per test, actual ASGI
receipt/custody routes and actual native permission/tenant adapter. External
HTTP is forbidden by the fixture. Only the authenticated-user boundary is
supplied by the test; database queries and identity mapping are not mocked.

Before the product fix, the six new route tests produced **3 failed, 3 passed,
2 subtests passed** in 7.38s (exit 1). Failures directly showed owner identity
returned instead of the receiving employee, owner's unrelated piece returned,
and a successfully received employee piece absent from custody.

After the fix, the affected suite produced **65 passed, 2 subtests passed** in
9.24s (exit 0), zero skipped/error/failure cases in JUnit:

```text
python -m pytest --noconftest -p no:cacheprovider -q --tb=short
  backend/tests/test_preparation_receiving_custody.py
  backend/tests/test_preparation_piece_operations.py
  backend/tests/test_mobile_app_request_context.py --asyncio-mode=auto
```

Tests cover receipts from the owner and two other preparers, owner compatibility,
receiver and merchant isolation, exclusion of assembled/cancelled/archived
pieces, repeated read without data mutation, receipt retry without duplicate
piece/event, live page-grant denial, and browser compatibility. The new suite is
registered in Review Completion Resumable CI with a minimum-six-case gate.
The permission-denial subtests independently restore the valid grant before
revocation. The final rerun also passed **65 tests and 2 subtests in 9.58s**
(exit 0). `git diff --check` passed. No skipped tests were accepted.

Code checkpoint `59d7d4466047a7be4e7575ff7a878c36ed771755` was pushed and
read-back verified. PR: https://github.com/AMASI-SA/AMASI-SA/pull/1434.
All 22 applicable PR checks passed; four explicitly nonapplicable checks were
skipped (manual redeploy, release-intent adapter rehearsal, two Snapchat-only
jobs). Release readiness is not inferred from those skipped release gates.
Review Completion Resumable run `39137444716` passed **442 tests and 259
subtests in 270.66s**, plus the separate acceptance guard **1 test in 2.04s**.
Both executed-suite gates reported zero skipped/failure/error cases.

### Status-filter extension validation

Five additional real-Mongo route tests reproduced the missing status filter
before the patch: the RED run exited 1 with **17 failures (including subtests),
8 passed tests and 4 passed subtests** in 15.84s. Completed/shipped pieces still
appeared, totals stayed unchanged and inactive order/barcode searches returned
200. The component-only reconciliation case already passed and stays covered.

After the filter, the focused suite passed **11 tests and 18 subtests** in
14.51s. The same affected-suite command above passed **70 tests and 18 subtests**
in 15.69s (exit 0). Local JUnit has zero skipped/failure/error cases. The CI
executed-case floor is increased from six to eleven for this suite.

New coverage includes external completion/shipping/cancellation/delivery for
both owner and employee receivers, mixed source-preparer cards, empty-card
removal, reopening without receipt rewrites, Arabic status aliases, missing and
contradictory status, explicit freshness invalidation, tenant isolation,
order/barcode search, and 120 pieces across six orders using one source read.
Database snapshots verify read requests do not modify stored business data.
Remote CI for this extension is pending the new source checkpoint.

## Release and remaining work

Production source/deployment changed: **no**. Production business writes: **no**.
No release lease, merge, APK build, OTA or live receipt was performed. The only
live interaction was read-only UI search and sidebar navigation on the emulator.
Hidden-customer-option diagnostics remain a separate unresolved task and are
not included. Existing shipping fixes are preserved by using the current
production base.

Next: review/integrate the source PR with the current release owner and follow
the complete v5 source/intent/guard protocol before any merge/deployment.
PR1433 was cancelled by the owner and closed unmerged; do not integrate it.
The current release owner coordinates mobile PR279 and the single next APK;
this task must not start a competing APK build. The hidden-option diagnostic
task remains separate. This is a backend read fix; it does not require
replacing the Android APK. Post-deployment readback with a real receiving
employee remains outstanding. The earlier disposable Mongo was stopped safely;
the isolated loopback replica was restarted for the status-filter tests and
must be stopped again after verification.
