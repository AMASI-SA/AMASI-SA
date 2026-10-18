# Preparation receiving custody actor

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
Source-preparer grouping is unchanged and includes both owner and non-owner
preparers. Tenant scope, own-receiver scope, permission checks, active custody
filters, source-employee snapshots and receipt idempotency remain unchanged.
There is no history rewrite, data migration, Android change or provider call.

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

## Release and remaining work

Production source/deployment changed: **no**. Production business writes: **no**.
No release lease, merge, APK build, OTA or live receipt was performed. The only
live interaction was read-only UI search and sidebar navigation on the emulator.
Hidden-customer-option diagnostics remain a separate unresolved task and are
not included. Existing shipping fixes are preserved by using the current
production base.

Next: review/integrate the source PR with the current release owner and follow
the complete v5 source/intent/guard protocol before any merge/deployment.
The release owner's PR1433 is separate; do not mix it or claim it includes this
fix without an exact combined source and fresh verification. The hidden-option
diagnostic task also remains separate. This is a backend read fix; it does not
require replacing the Android APK. Post-deployment readback with a real
receiving employee remains outstanding. The disposable test Mongo was stopped
gracefully after checking its executable, start time and replica identity.
